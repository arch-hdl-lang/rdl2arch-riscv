"""Emit the PLIC logic module.

Generic rdl2arch emits the MMIO register block (priority / pending /
enable / threshold / claim). This module emits the sibling that does
priority arbitration over the source bitmap each cycle and drives
both `hwif_in.claim_value` (winner ID, read by SW) and `meip_out`
(→ CSR file's `mip.meip`).

v1 arbitration: for each source `i` in 1..N,
    candidate[i] = source_in[i] && enable[i] && priority[i] > threshold
The winner is the candidate with the highest priority, breaking ties
toward the lowest ID. Emitted as a linear cascade of `let` bindings —
unrolled per source count at generation time. N=8 gives ~8 tuples of
`(id, prio)` state, plus one `comb` block tying it together.

Scope limits (documented in `udps/plic.py`): single context,
level-triggered sources, read-only claim.
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

    Emits one arbitration cascade per context; the winner per context is
    packed into a single `intr_out: out UInt<N_contexts>` port (bit i =
    context i's meip/seip/... output).
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

    # Winner-ID width: enough to hold values 0..n.
    id_w = max(1, n.bit_length())
    prio_w = 3  # fixture-chosen; generator could derive from RDL width.

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
    lines.append("")

    # Per-context arbitration cascade.
    for ctx in range(n_ctx):
        lines.append(f"  // ── context {ctx} ──")
        for i in range(1, n + 1):
            lines.append(
                f"  let c{ctx}_cand_{i}: Bool = source_in[{i}] and "
                f"hwif_out.{enable_hwif(ctx)}[{i}] and "
                f"(hwif_out.{prio_hwif(i)} > hwif_out.{threshold_hwif(ctx)});"
            )
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
