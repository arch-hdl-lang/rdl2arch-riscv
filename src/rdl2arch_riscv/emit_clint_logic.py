"""Emit the CLINT logic module.

The generic rdl2arch emits the MMIO register block for CLINT (address
decode, cpuif handshake, hwif structs). This module emits the thin
sibling that glues the reg block to the CPU's mip bits:

  * msip_out[hart]  drives  mip.msip   — pure passthrough of bit 0.
  * mtip_out[hart]  drives  mip.mtip   — set when the 64-bit mtime
                                         reaches mtimecmp[hart].
  * mtime            counts up one per cycle that the `mtime_tick`
                      input is high (upstream clock divider, or tie
                      high for full-speed).

v1 ships a single-hart layout. Multi-hart adds an indexed port bundle
per hart — straightforward extension that doesn't change the core
arithmetic.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Optional

from systemrdl.node import AddrmapNode, RegNode


@dataclass
class ClintModel:
    top: AddrmapNode
    module_name: str
    package_name: str
    hwif_in_struct: str
    hwif_out_struct: str
    msip: Optional[RegNode] = None
    mtimecmp_lo: Optional[RegNode] = None
    mtimecmp_hi: Optional[RegNode] = None
    mtime_lo: Optional[RegNode] = None
    mtime_hi: Optional[RegNode] = None


def scan_clint(top: AddrmapNode, module_name: str, package_name: str) -> ClintModel:
    """Walk the elaborated addrmap and bucket each reg by its CLINT role."""
    model = ClintModel(
        top=top,
        module_name=module_name,
        package_name=package_name,
        hwif_in_struct=module_name + "HwifIn",
        hwif_out_struct=module_name + "HwifOut",
    )
    for child in top.children(unroll=False):
        if not isinstance(child, RegNode):
            continue
        role = child.get_property("riscv_intr_clint_role")
        if role is None:
            continue
        if role == "msip":
            model.msip = child
        elif role == "mtimecmp_lo":
            model.mtimecmp_lo = child
        elif role == "mtimecmp_hi":
            model.mtimecmp_hi = child
        elif role == "mtime_lo":
            model.mtime_lo = child
        elif role == "mtime_hi":
            model.mtime_hi = child
    return model


def _reg_field_hwif_out(reg: RegNode, field_name: str) -> str:
    """`<reg_inst_name>_<field_name>` — the hwif_out member name used by rdl2arch."""
    return f"{reg.inst_name}_{field_name}"


def emit_clint_logic(model: ClintModel, logic_module_name: str) -> str:
    """Generate the ClintLogic .arch source."""
    if model.msip is None:
        raise ValueError("CLINT fixture is missing an `msip` reg (role = 'msip')")
    if model.mtimecmp_lo is None or model.mtimecmp_hi is None:
        raise ValueError(
            "CLINT fixture must declare both mtimecmp_lo and mtimecmp_hi regs "
            "(role = 'mtimecmp_lo' / 'mtimecmp_hi')"
        )
    if model.mtime_lo is None or model.mtime_hi is None:
        raise ValueError(
            "CLINT fixture must declare both mtime_lo and mtime_hi regs "
            "(role = 'mtime_lo' / 'mtime_hi')"
        )

    # The msip reg uses field name `value` for bit 0 by convention (fixture
    # declares it that way). mtimecmp / mtime use `v` for the 32-bit data
    # field. Pick up the field names directly from the RDL model so a user
    # who picks different names still gets correct hwif_out references.
    msip_field = next(iter(model.msip.fields())).inst_name
    mtcmp_lo_field = next(iter(model.mtimecmp_lo.fields())).inst_name
    mtcmp_hi_field = next(iter(model.mtimecmp_hi.fields())).inst_name
    mtime_lo_field = next(iter(model.mtime_lo.fields())).inst_name
    mtime_hi_field = next(iter(model.mtime_hi.fields())).inst_name

    msip_hwif     = _reg_field_hwif_out(model.msip,          msip_field)
    mtcmp_lo_hwif = _reg_field_hwif_out(model.mtimecmp_lo,   mtcmp_lo_field)
    mtcmp_hi_hwif = _reg_field_hwif_out(model.mtimecmp_hi,   mtcmp_hi_field)
    mtime_lo_hwif = _reg_field_hwif_out(model.mtime_lo,      mtime_lo_field)
    mtime_hi_hwif = _reg_field_hwif_out(model.mtime_hi,      mtime_hi_field)

    lines: list[str] = []
    lines.append(f"use {model.package_name};")
    lines.append("")
    lines.append(f"module {logic_module_name}")
    lines.append("  port clk:        in Clock<SysDomain>;")
    lines.append("  port rst:        in Reset<Sync>;")
    lines.append("  port mtime_tick: in Bool;")
    lines.append(f"  port hwif_out:   in  {model.hwif_out_struct};")
    lines.append(f"  port hwif_in:    out {model.hwif_in_struct};")
    lines.append("  port msip_out:   out Bool;")
    lines.append("  port mtip_out:   out Bool;")
    lines.append("")
    # Module-scope `let`s for the concatenated 64-bit timer and comparator.
    lines.append(
        f"  let mtime:    UInt<64> = {{hwif_out.{mtime_hi_hwif}, hwif_out.{mtime_lo_hwif}}};"
    )
    lines.append(
        f"  let mtimecmp: UInt<64> = {{hwif_out.{mtcmp_hi_hwif}, hwif_out.{mtcmp_lo_hwif}}};"
    )
    # Wrap-add keeps the 64-bit width without an explicit `.trunc<64>()`.
    lines.append(
        "  let next_mtime: UInt<64> = mtime_tick ? (mtime +% 64'h1) : mtime;"
    )
    lines.append("")
    lines.append("  comb")
    # mtime writeback to the reg block — sw-write-wins handled by the CSR
    # file's seq precedence (sw write in the same cycle takes priority).
    lines.append(f"    hwif_in.{mtime_lo_hwif} = next_mtime[31:0];")
    lines.append(f"    hwif_in.{mtime_hi_hwif} = next_mtime[63:32];")
    # msip is 1 bit — `hwif_out.msip_value` has type UInt<1>. Compare !=0
    # to get a Bool cleanly.
    lines.append(f"    msip_out = hwif_out.{msip_hwif} != 1'h0;")
    lines.append("    mtip_out = mtime >= mtimecmp;")
    lines.append("  end comb")
    lines.append(f"end module {logic_module_name}")
    lines.append("")
    return "\n".join(lines)
