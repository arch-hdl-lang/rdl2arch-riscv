"""Emit the PLIC logic module.

Generic rdl2arch emits the MMIO register block (priority / pending /
enable / threshold / claim). This module emits the sibling that does
priority arbitration over the source bitmap each cycle AND runs the
spec-compliant claim / complete handshake, driving both
`hwif_in.claim_<ctx>_value` (winner ID, read by SW) and
`intr_out` (→ CSR file's `mip.meip` / `mip.seip`).

Arbitration: for each source `i` in 1..N and each context `ctx`,
    cand[ctx][i] = source_in[i] && enable[ctx][i]
                   && priority[i] > threshold[ctx]
                   && !claimed[ctx][i]
The winner per context is the candidate with the highest priority,
breaking ties toward the lowest ID. Emitted as a linear cascade of
`let` bindings, unrolled per source count at generation time.

Claim / complete (SiFive semantics, consumed via the upstream rdl2arch
`emit_read_pulse` / `emit_write_pulse` UDPs):

  * SW **read** of the per-context claim register
    → 1-cycle `claim_<ctx>_read_pulse` pulse → the logic module latches
      `claimed[ctx][hwif_out.claim_<ctx>_value] <= 1`, masking that
      source from further arbitration on this context.

  * SW **write** of the same register
    → 1-cycle `claim_<ctx>_write_pulse` pulse → delayed internally by
      one cycle so the storage update has propagated into
      `hwif_out.claim_<ctx>_value` → the logic module clears the
      matching in-service bit, re-enabling that source.

Scope limits (documented in `udps/plic.py`):
  * Level-triggered sources only — `pending` is a straight passthrough
    of `source_in`, no edge detection.
  * Edge detection and S-mode delegation are scoped to follow-ups.
"""

from __future__ import annotations

from dataclasses import dataclass, field as dc_field
from typing import Optional

from systemrdl.node import AddrmapNode, RegNode


@dataclass
class PlicModel:
    top: AddrmapNode
    module_name: str
    package_name: str
    hwif_in_struct: str
    hwif_out_struct: str
    pending: Optional[RegNode] = None
    # Per-context registers — index in each list = context ID. All three
    # lists must end up the same length (n_contexts); the scanner sorts
    # by absolute address so the ordering matches the MMIO layout.
    enables: list[RegNode] = dc_field(default_factory=list)
    thresholds: list[RegNode] = dc_field(default_factory=list)
    claims: list[RegNode] = dc_field(default_factory=list)
    priorities: list[RegNode] = dc_field(default_factory=list)
    n_sources: int = 0  # inclusive count of source IDs we've seen priority regs for

    @property
    def n_contexts(self) -> int:
        return len(self.thresholds)


def scan_plic(top: AddrmapNode, module_name: str, package_name: str) -> PlicModel:
    """Walk the addrmap and bucket each reg by its PLIC role.

    Multi-context fixtures declare one enable / threshold / claim reg
    per context. Scanner sorts each by absolute address so context 0 is
    the lowest-addressed one (matches SiFive convention: M-mode first,
    then S-mode, etc.).
    """
    model = PlicModel(
        top=top,
        module_name=module_name,
        package_name=package_name,
        hwif_in_struct=module_name + "HwifIn",
        hwif_out_struct=module_name + "HwifOut",
    )
    priorities: list[RegNode] = []
    enables: list[RegNode] = []
    thresholds: list[RegNode] = []
    claims: list[RegNode] = []
    # children(unroll=True) expands reg arrays into individual RegNode
    # instances, which is what we want for priority[N].
    for child in top.children(unroll=True):
        if not isinstance(child, RegNode):
            continue
        role = child.get_property("riscv_intr_plic_role")
        if role is None:
            continue
        if role == "priority":
            priorities.append(child)
        elif role == "pending":
            model.pending = child
        elif role == "enable":
            enables.append(child)
        elif role == "threshold":
            thresholds.append(child)
        elif role == "claim":
            claims.append(child)
    # Sort priorities by array index (source ID).
    def _idx(r: RegNode) -> int:
        if r.current_idx is not None:
            return int(r.current_idx[-1])
        return 0
    priorities.sort(key=_idx)
    # Sort per-context regs by absolute address (context ID ordering).
    enables.sort(key=lambda r: r.absolute_address)
    thresholds.sort(key=lambda r: r.absolute_address)
    claims.sort(key=lambda r: r.absolute_address)
    model.priorities = priorities
    model.enables = enables
    model.thresholds = thresholds
    model.claims = claims
    model.n_sources = len(priorities) - 1  # exclude the reserved source 0
    return model


def _validate(model: PlicModel) -> None:
    if model.pending is None:
        raise ValueError("PLIC fixture missing a `pending` reg.")
    for name in ("enables", "thresholds", "claims"):
        if not getattr(model, name):
            raise ValueError(
                f"PLIC fixture missing `{name[:-1]}` reg(s). Each context "
                f"needs its own enable / threshold / claim."
            )
    n_ctx = model.n_contexts
    if len(model.enables) != n_ctx or len(model.claims) != n_ctx:
        raise ValueError(
            f"PLIC context regs must match in count: got "
            f"enables={len(model.enables)}, thresholds={len(model.thresholds)}, "
            f"claims={len(model.claims)}"
        )
    if model.n_sources < 1:
        raise ValueError(
            "PLIC fixture must declare at least one priority reg "
            "beyond the reserved source 0 (saw "
            f"{len(model.priorities)} total)."
        )
    # Each claim reg MUST opt in to both pulse UDPs — the logic module
    # relies on read / write pulses to drive the claim / complete state.
    for ctx, claim in enumerate(model.claims):
        if not bool(claim.get_property("emit_read_pulse") or False):
            raise ValueError(
                f"PLIC claim reg `{claim.inst_name}` (context {ctx}) must "
                f"set `emit_read_pulse = true;` — the logic module uses "
                f"the read pulse to latch the claim (in-service) bit."
            )
        if not bool(claim.get_property("emit_write_pulse") or False):
            raise ValueError(
                f"PLIC claim reg `{claim.inst_name}` (context {ctx}) must "
                f"set `emit_write_pulse = true;` — the logic module uses "
                f"the write pulse to clear the in-service bit on complete."
            )


def _reg_field_name(reg: RegNode, field_inst_name: str) -> str:
    """`<reg_inst_name>[_<idx>]*_<field_name>` — the hwif member name used
    by rdl2arch.

    For a plain reg, this is just `<inst_name>_<field_name>`. For an
    indexed array (e.g. `priority[3]`), `reg.inst_name` is the base
    `priority` and `reg.current_idx` carries the index tuple — rdl2arch
    formats the hwif member as `priority_3_value`.
    """
    parts = [reg.inst_name]
    if reg.current_idx:
        parts.extend(str(i) for i in reg.current_idx)
    parts.append(field_inst_name)
    return "_".join(parts)


def emit_plic_logic(model: PlicModel, logic_module_name: str) -> str:
    """Generate the PlicLogic .arch source.

    Emits one arbitration cascade per context, an `in-service` state
    register per context (cleared on reset, set by a claim read, cleared
    by the matching complete write), and packs the per-context winner
    into a single `intr_out: out UInt<N_contexts>` port (bit i = context
    i's meip/seip/... output).
    """
    _validate(model)

    n = model.n_sources
    n_ctx = model.n_contexts

    # Field names (RDL-supplied; not hard-coded).
    pending_fld = next(iter(model.pending.fields())).inst_name
    enable_fld = next(iter(model.enables[0].fields())).inst_name
    threshold_fld = next(iter(model.thresholds[0].fields())).inst_name
    claim_fld = next(iter(model.claims[0].fields())).inst_name
    priority_fld = next(iter(model.priorities[0].fields())).inst_name

    pending_hwif = _reg_field_name(model.pending, pending_fld)

    def enable_hwif(ctx: int) -> str:
        return _reg_field_name(model.enables[ctx], enable_fld)
    def threshold_hwif(ctx: int) -> str:
        return _reg_field_name(model.thresholds[ctx], threshold_fld)
    def claim_hwif(ctx: int) -> str:
        return _reg_field_name(model.claims[ctx], claim_fld)
    def prio_hwif(i: int) -> str:
        return _reg_field_name(model.priorities[i], priority_fld)

    # Pulse port names come straight from the RDL reg's inst_name — matches
    # how rdl2arch's emit_regblock names the emitted output ports.
    def claim_read_pulse(ctx: int) -> str:
        return f"{model.claims[ctx].inst_name}_read_pulse"
    def claim_write_pulse(ctx: int) -> str:
        return f"{model.claims[ctx].inst_name}_write_pulse"

    # Winner-ID width: enough to hold values 0..n.
    id_w = max(1, n.bit_length())
    prio_w = 3  # fixture-chosen; generator could derive from RDL width.

    # In-service bitmap width (one bit per source ID, incl. the reserved 0).
    bits = n + 1
    # Hex digits needed to write a `bits`-wide literal.
    hex_digits = (bits + 3) // 4

    def claim_mask_lit(i: int) -> str:
        """`{bits}'h<hex>` for 1<<i — used in the set / clear match arms."""
        return f"{bits}'h{(1 << i):0{hex_digits}x}"
    zero_mask = f"{bits}'h{0:0{hex_digits}x}"

    lines: list[str] = []
    lines.append(f"use {model.package_name};")
    lines.append("")
    lines.append(f"module {logic_module_name}")
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Sync>;")
    lines.append(f"  port source_in: in UInt<{n + 1}>;")
    lines.append(f"  port hwif_out:  in  {model.hwif_out_struct};")
    lines.append(f"  port hwif_in:   out {model.hwif_in_struct};")
    lines.append(f"  port intr_out:  out UInt<{n_ctx}>;")
    # Pulse inputs from the register block — one read + one write per context.
    for ctx in range(n_ctx):
        lines.append(f"  port {claim_read_pulse(ctx)}:  in Bool;")
        lines.append(f"  port {claim_write_pulse(ctx)}: in Bool;")
    lines.append("")
    lines.append("  default seq on clk rising;")
    lines.append("")

    # In-service state + 1-cycle delayed write pulse per context.
    for ctx in range(n_ctx):
        lines.append(
            f"  reg c{ctx}_claimed_r: UInt<{bits}> reset rst => {zero_mask};"
        )
        lines.append(
            f"  reg c{ctx}_wr_pulse_d: Bool reset rst => false;"
        )
    lines.append("")

    # Per-context arbitration cascade + claim/complete bookkeeping.
    for ctx in range(n_ctx):
        lines.append(f"  // ── context {ctx} ──")
        # Candidates: source pending & enabled & priority > threshold
        # & not currently in service on this context.
        for i in range(1, n + 1):
            lines.append(
                f"  let c{ctx}_cand_{i}: Bool = source_in[{i}] and "
                f"hwif_out.{enable_hwif(ctx)}[{i}] and "
                f"(hwif_out.{prio_hwif(i)} > hwif_out.{threshold_hwif(ctx)}) "
                f"and (c{ctx}_claimed_r[{i}] == 1'b0);"
            )
        # Cascade: running-best (id, prio) tuple, lowest-ID tiebreak via
        # strict `>` on update.
        lines.append(
            f"  let c{ctx}_w1_id:   UInt<{id_w}> = c{ctx}_cand_1 ? {id_w}'h1 : {id_w}'h0;"
        )
        lines.append(
            f"  let c{ctx}_w1_prio: UInt<{prio_w}> = c{ctx}_cand_1 ? "
            f"hwif_out.{prio_hwif(1)} : {prio_w}'h0;"
        )
        for i in range(2, n + 1):
            lines.append(
                f"  let c{ctx}_w{i}_take: Bool = c{ctx}_cand_{i} and "
                f"(c{ctx}_w{i-1}_id == {id_w}'h0 or hwif_out.{prio_hwif(i)} > c{ctx}_w{i-1}_prio);"
            )
            lines.append(
                f"  let c{ctx}_w{i}_id:   UInt<{id_w}> = c{ctx}_w{i}_take ? "
                f"{id_w}'h{i:x} : c{ctx}_w{i-1}_id;"
            )
            lines.append(
                f"  let c{ctx}_w{i}_prio: UInt<{prio_w}> = c{ctx}_w{i}_take ? "
                f"hwif_out.{prio_hwif(i)} : c{ctx}_w{i-1}_prio;"
            )
        # Claim set mask — derived from the current stored claim reg
        # value (= the winner HW drove into storage last cycle, and
        # therefore the value SW sees on the read). Emitted as an
        # unrolled match so we don't rely on runtime-variable shifts.
        lines.append(
            f"  let c{ctx}_set_bit: UInt<{bits}> = match hwif_out.{claim_hwif(ctx)}"
        )
        for i in range(1, n + 1):
            lines.append(f"    {id_w}'h{i:x} => {claim_mask_lit(i)},")
        lines.append(f"    _    => {zero_mask}")
        lines.append("  end match;")
        lines.append(
            f"  let c{ctx}_set_mask: UInt<{bits}> = "
            f"{claim_read_pulse(ctx)} ? c{ctx}_set_bit : {zero_mask};"
        )
        # Complete clear mask — 1-cycle delayed so the SW-written ID
        # has propagated through storage into hwif_out.
        lines.append(
            f"  let c{ctx}_clr_bit: UInt<{bits}> = match hwif_out.{claim_hwif(ctx)}"
        )
        for i in range(1, n + 1):
            lines.append(f"    {id_w}'h{i:x} => {claim_mask_lit(i)},")
        lines.append(f"    _    => {zero_mask}")
        lines.append("  end match;")
        lines.append(
            f"  let c{ctx}_clr_mask: UInt<{bits}> = "
            f"c{ctx}_wr_pulse_d ? c{ctx}_clr_bit : {zero_mask};"
        )
        lines.append("")

    # Sequential updates — delay the write pulse one cycle and fold both
    # set and clear masks into the in-service state.
    lines.append("  seq")
    for ctx in range(n_ctx):
        lines.append(
            f"    c{ctx}_wr_pulse_d <= {claim_write_pulse(ctx)};"
        )
        lines.append(
            f"    c{ctx}_claimed_r <= "
            f"(c{ctx}_claimed_r | c{ctx}_set_mask) & (~c{ctx}_clr_mask);"
        )
    lines.append("  end seq")
    lines.append("")

    lines.append("  comb")
    # Pending register passthrough — SW-visible view of source_in.
    lines.append(f"    hwif_in.{pending_hwif} = source_in;")
    # Per-context claim registers + intr_out bits.
    for ctx in range(n_ctx):
        lines.append(f"    hwif_in.{claim_hwif(ctx)} = c{ctx}_w{n}_id;")
    # intr_out[i] = context i has a winner. Build as a concat literal —
    # emitted MSB-first per ARCH convention so bit 0 (context 0) is the
    # last element in the `{...}` expression.
    fired = ", ".join(
        f"(c{ctx}_w{n}_id != {id_w}'h0)"
        for ctx in range(n_ctx - 1, -1, -1)
    )
    if n_ctx == 1:
        lines.append(f"    intr_out = c0_w{n}_id != {id_w}'h0;")
    else:
        lines.append(f"    intr_out = {{{fired}}};")
    lines.append("  end comb")
    lines.append(f"end module {logic_module_name}")
    lines.append("")
    return "\n".join(lines)
