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
    enable: Optional[RegNode] = None
    threshold: Optional[RegNode] = None
    claim: Optional[RegNode] = None
    priorities: list[RegNode] = dc_field(default_factory=list)
    n_sources: int = 0  # inclusive count of source IDs we've seen priority regs for


def scan_plic(top: AddrmapNode, module_name: str, package_name: str) -> PlicModel:
    """Walk the addrmap and bucket each reg by its PLIC role."""
    model = PlicModel(
        top=top,
        module_name=module_name,
        package_name=package_name,
        hwif_in_struct=module_name + "HwifIn",
        hwif_out_struct=module_name + "HwifOut",
    )
    priorities: list[RegNode] = []
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
            model.enable = child
        elif role == "threshold":
            model.threshold = child
        elif role == "claim":
            model.claim = child
    # Sort priority registers by their array index so source i lines up
    # with priorities[i]. systemrdl gives us inst_name like
    # "priority[0]" / "priority[1]" / ... ; sort on numeric suffix.
    def _idx(r: RegNode) -> int:
        # Each instance of an indexed reg has current_idx set.
        if r.current_idx is not None:
            return int(r.current_idx[-1])
        return 0
    priorities.sort(key=_idx)
    model.priorities = priorities
    model.n_sources = len(priorities) - 1  # exclude the reserved source 0
    return model


def _validate(model: PlicModel) -> None:
    missing = []
    for name in ("pending", "enable", "threshold", "claim"):
        if getattr(model, name) is None:
            missing.append(name)
    if missing:
        raise ValueError(
            f"PLIC fixture is missing reg(s) with role(s): {missing}. "
            f"Each PLIC needs exactly one of pending / enable / threshold / claim."
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
    """Generate the PlicLogic .arch source."""
    _validate(model)

    n = model.n_sources  # e.g. 8

    # Pick field names from the RDL model (user-chosen; the fixture uses
    # `value` consistently but we shouldn't hard-code that).
    pending_fld = next(iter(model.pending.fields())).inst_name
    enable_fld = next(iter(model.enable.fields())).inst_name
    threshold_fld = next(iter(model.threshold.fields())).inst_name
    claim_fld = next(iter(model.claim.fields())).inst_name
    priority_fld = next(iter(model.priorities[0].fields())).inst_name

    pending_hwif   = _reg_field_name(model.pending,   pending_fld)
    enable_hwif    = _reg_field_name(model.enable,    enable_fld)
    threshold_hwif = _reg_field_name(model.threshold, threshold_fld)
    claim_hwif     = _reg_field_name(model.claim,     claim_fld)

    def prio_hwif(i: int) -> str:
        return _reg_field_name(model.priorities[i], priority_fld)

    # Winner-ID width: enough to hold values 0..n.
    id_bits = max(1, (n).bit_length())
    if n + 1 <= (1 << id_bits):
        # Fine as-is.
        pass
    id_w = id_bits
    prio_w = 3  # hard-coded by the fixture's field width for now; could
                # derive from the RDL if we let the user pick other widths.

    lines: list[str] = []
    lines.append(f"use {model.package_name};")
    lines.append("")
    lines.append(f"module {logic_module_name}")
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Sync>;")
    lines.append(f"  port source_in: in UInt<{n + 1}>;")
    lines.append(f"  port hwif_out:  in  {model.hwif_out_struct};")
    lines.append(f"  port hwif_in:   out {model.hwif_in_struct};")
    lines.append("  port meip_out:  out Bool;")
    lines.append("")

    # Per-source candidate flags.
    for i in range(1, n + 1):
        lines.append(
            f"  let cand_{i}: Bool = source_in[{i}] and "
            f"hwif_out.{enable_hwif}[{i}] and "
            f"(hwif_out.{prio_hwif(i)} > hwif_out.{threshold_hwif});"
        )
    lines.append("")

    # Linear cascade: maintain `(w<i>_id, w<i>_prio)` = best candidate among
    # sources 1..i. Tie-break toward the lowest ID (strict `>` on the
    # update predicate keeps the earlier source on equal priority).
    lines.append(
        f"  let w1_id:   UInt<{id_w}> = cand_1 ? {id_w}'h1 : {id_w}'h0;"
    )
    lines.append(
        f"  let w1_prio: UInt<{prio_w}> = cand_1 ? hwif_out.{prio_hwif(1)} : {prio_w}'h0;"
    )
    for i in range(2, n + 1):
        lines.append(
            f"  let w{i}_take: Bool = cand_{i} and "
            f"(w{i-1}_id == {id_w}'h0 or hwif_out.{prio_hwif(i)} > w{i-1}_prio);"
        )
        lines.append(
            f"  let w{i}_id:   UInt<{id_w}> = w{i}_take ? {id_w}'h{i:x} : w{i-1}_id;"
        )
        lines.append(
            f"  let w{i}_prio: UInt<{prio_w}> = w{i}_take ? hwif_out.{prio_hwif(i)} : w{i-1}_prio;"
        )
    lines.append("")

    lines.append("  comb")
    # Pending register passthrough — SW-visible view of current source levels.
    lines.append(f"    hwif_in.{pending_hwif} = source_in;")
    # Winner ID → claim reg (SW reads it).
    lines.append(f"    hwif_in.{claim_hwif} = w{n}_id;")
    # → CSR file's mip.meip
    lines.append(f"    meip_out = w{n}_id != {id_w}'h0;")
    lines.append("  end comb")
    lines.append(f"end module {logic_module_name}")
    lines.append("")
    return "\n".join(lines)
