"""Emit the trap-coordinator ARCH module (Module 3 of the plan).

Responsibilities (Phase 3 MVP):

1. For every field tagged `riscv_save_on_trap`, snapshot a pipeline-provided
   input into the CSR file on the `trap_enter` cycle. Between traps, the
   field's hwif_in is pass-through from `hwif_in_live`.

2. For fields that are hw-writable but NOT save-on-trap, pass-through from
   `hwif_in_live` to `hwif_in_drive` unchanged. The pipeline can route
   hwif_in_live from the CSR file's own `hwif_out` (no-op) or compute
   non-trap updates itself (e.g. MIE toggles on trap entry / xRET).

3. `riscv_restore_on_ret`: Phase 3 MVP does NOT emit new ports for restore.
   The restore semantics (which live field gets written from the saved
   field on xRET) are CPU-design-specific; the user reads hwif_out values
   from the CSR file and drives hwif_in_live externally.

Interface:

  port clk: in Clock<SysDomain>;
  port rst: in Reset<Sync>;
  port trap_enter:      in Bool;
  port save_<member>:   in UInt<W>    per save-on-trap field, W = field width
  port hwif_in_live:    in <HwifIn>;
  port hwif_in_drive:   out <HwifIn>;
"""

from .scan_csrs import CsrDesignModel, CsrFieldModel, CsrRegModel


def _save_port_name(reg: CsrRegModel, field: CsrFieldModel) -> str:
    """`save_<reg>_<field>` matches the hwif member naming convention."""
    return f"save_{reg.name}_{field.name}"


def _all_hwif_in_members(design: CsrDesignModel) -> list[tuple[CsrRegModel, CsrFieldModel]]:
    """Every (reg, field) that appears as a member in the HwifIn struct."""
    return [
        (reg, f) for reg in design.regs for f in reg.fields if f.hw_writable
    ]


def _save_on_trap_fields(design: CsrDesignModel) -> list[tuple[CsrRegModel, CsrFieldModel]]:
    return [
        (reg, f) for reg in design.regs for f in reg.fields if f.save_on_trap
    ]


def emit_trap_coordinator(design: CsrDesignModel, module_name: str) -> str:
    lines: list[str] = []
    lines.append(f"use {design.package_name};")
    lines.append("")
    lines.append(f"module {module_name}")
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Sync>;")
    lines.append("  port trap_enter: in Bool;")

    # One save_ port per save_on_trap field.
    save_fields = _save_on_trap_fields(design)
    for reg, f in save_fields:
        port = _save_port_name(reg, f)
        lines.append(f"  port {port}: in UInt<{f.width}>;")

    lines.append(f"  port hwif_in_live:  in  {design.hwif_in_struct};")
    lines.append(f"  port hwif_in_drive: out {design.hwif_in_struct};")
    lines.append("")
    lines.append("  comb")

    all_hwif = _all_hwif_in_members(design)
    save_set = {(reg.name, f.name) for reg, f in save_fields}

    for reg, f in all_hwif:
        member = f"{reg.name}_{f.name}"
        if (reg.name, f.name) in save_set:
            save_port = _save_port_name(reg, f)
            lines.append(
                f"    hwif_in_drive.{member} = "
                f"trap_enter ? {save_port} : hwif_in_live.{member};"
            )
        else:
            lines.append(
                f"    hwif_in_drive.{member} = hwif_in_live.{member};"
            )

    if not all_hwif:
        # Defensive: if the design has no hw_writable fields, the HwifIn
        # struct has a `_reserved` placeholder that still needs a driver.
        lines.append("    hwif_in_drive._reserved = hwif_in_live._reserved;")

    lines.append("  end comb")
    lines.append(f"end module {module_name}")
    lines.append("")
    return "\n".join(lines)
