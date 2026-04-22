"""Emit the trap-coordinator ARCH module (Module 3 of the plan).

Responsibilities:

1. For every field tagged `riscv_save_on_trap`, snapshot a pipeline-provided
   input into the CSR file on the `trap_enter` cycle. Between traps, the
   field's hwif_in is pass-through from `hwif_in_live`.

2. For every field tagged `riscv_restore_on_ret`, snapshot a pipeline-
   provided input into the CSR file on the `xret_enter` cycle (mret /
   sret / dret). Same shape as save-on-trap but in the opposite direction
   of the trap lifecycle. The pipeline computes the restore value
   externally — for mstatus on a typical RISC-V core that's
   `mie ← mpie`, `mpie ← 1`, `mpp ← priv_lvl_min`, all handed in via
   the per-field `restore_<member>` ports — and the TrapCoord routes it
   into `hwif_in_drive` with priority `trap_enter > xret_enter > live`.
   `trap_enter` and `xret_enter` are specified to be mutually exclusive
   by the RISC-V spec, so the priority order is a safety belt; either
   ordering would produce the same behaviour in a spec-conformant
   pipeline.

3. For every field tagged `riscv_hw_mirror`, drive `hwif_in_drive` from
   a dedicated `mirror_<member>` input port every cycle — unconditional,
   no save/restore gating. This is the "live-mirror external signal"
   mode for fields like `mip.msip` that just track an incoming IRQ
   wire; validation rejects combining it with save_on_trap or
   restore_on_ret (mixing always-on and event-gated drives would leave
   non-event behaviour undefined).

4. For fields that are hw-writable but have none of the above tags,
   pass-through from `hwif_in_live` to `hwif_in_drive` unchanged. The
   pipeline can route `hwif_in_live` from the CSR file's own
   `hwif_out` (hold) or compute non-lifecycle updates itself (e.g.
   mstatus.MIE auto-clear on trap entry).

Interface:

  port clk: in Clock<SysDomain>;
  port rst: in Reset<Async, Low>;
  port trap_enter:        in Bool;
  port xret_enter:        in Bool;
  port save_<member>:     in UInt<W>   per save-on-trap field, W = field width
  port restore_<member>:  in UInt<W>   per restore-on-ret field
  port mirror_<member>:   in UInt<W>   per hw-mirror field
  port hwif_in_live:      in  <HwifIn>;
  port hwif_in_drive:     out <HwifIn>;
"""

from .scan_csrs import CsrDesignModel, CsrFieldModel, CsrRegModel


def _save_port_name(reg: CsrRegModel, field: CsrFieldModel) -> str:
    """`save_<reg>_<field>` matches the hwif member naming convention."""
    return f"save_{reg.name}_{field.name}"


def _restore_port_name(reg: CsrRegModel, field: CsrFieldModel) -> str:
    """`restore_<reg>_<field>` — symmetric counterpart to `save_…`."""
    return f"restore_{reg.name}_{field.name}"


def _mirror_port_name(reg: CsrRegModel, field: CsrFieldModel) -> str:
    """`mirror_<reg>_<field>` — always-on live drive from external signal."""
    return f"mirror_{reg.name}_{field.name}"


def _all_hwif_in_members(design: CsrDesignModel) -> list[tuple[CsrRegModel, CsrFieldModel]]:
    """Every (reg, field) that appears as a member in the HwifIn struct."""
    return [
        (reg, f) for reg in design.regs for f in reg.fields if f.hw_writable
    ]


def _save_on_trap_fields(design: CsrDesignModel) -> list[tuple[CsrRegModel, CsrFieldModel]]:
    return [
        (reg, f) for reg in design.regs for f in reg.fields if f.save_on_trap
    ]


def _restore_on_ret_fields(design: CsrDesignModel) -> list[tuple[CsrRegModel, CsrFieldModel]]:
    return [
        (reg, f) for reg in design.regs for f in reg.fields if f.restore_on_ret
    ]


def _hw_mirror_fields(design: CsrDesignModel) -> list[tuple[CsrRegModel, CsrFieldModel]]:
    return [
        (reg, f) for reg in design.regs for f in reg.fields if f.hw_mirror
    ]


def emit_trap_coordinator(design: CsrDesignModel, module_name: str) -> str:
    lines: list[str] = []
    lines.append(f"use {design.package_name};")
    lines.append("")
    lines.append(f"module {module_name}")
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Async, Low>;")
    lines.append("  port trap_enter: in Bool;")
    lines.append("  port xret_enter: in Bool;")

    save_fields = _save_on_trap_fields(design)
    for reg, f in save_fields:
        port = _save_port_name(reg, f)
        lines.append(f"  port {port}: in UInt<{f.width}>;")

    restore_fields = _restore_on_ret_fields(design)
    for reg, f in restore_fields:
        port = _restore_port_name(reg, f)
        lines.append(f"  port {port}: in UInt<{f.width}>;")

    mirror_fields = _hw_mirror_fields(design)
    for reg, f in mirror_fields:
        port = _mirror_port_name(reg, f)
        lines.append(f"  port {port}: in UInt<{f.width}>;")

    lines.append(f"  port hwif_in_live:  in  {design.hwif_in_struct};")
    lines.append(f"  port hwif_in_drive: out {design.hwif_in_struct};")
    lines.append("")
    lines.append("  comb")

    all_hwif = _all_hwif_in_members(design)
    save_set = {(reg.name, f.name) for reg, f in save_fields}
    restore_set = {(reg.name, f.name) for reg, f in restore_fields}
    mirror_set = {(reg.name, f.name) for reg, f in mirror_fields}

    for reg, f in all_hwif:
        member = f"{reg.name}_{f.name}"
        live_expr = f"hwif_in_live.{member}"
        has_save = (reg.name, f.name) in save_set
        has_restore = (reg.name, f.name) in restore_set
        has_mirror = (reg.name, f.name) in mirror_set

        if has_mirror:
            # Validation guarantees no co-tagging with save/restore here,
            # so the mirror drive is unconditional.
            lines.append(
                f"    hwif_in_drive.{member} = {_mirror_port_name(reg, f)};"
            )
        elif has_save and has_restore:
            # Priority: trap_enter > xret_enter > live. Spec says the
            # two pulses are mutually exclusive, so the order is a
            # safety belt rather than a semantic knob.
            lines.append(
                f"    hwif_in_drive.{member} = "
                f"trap_enter ? {_save_port_name(reg, f)} : "
                f"xret_enter ? {_restore_port_name(reg, f)} : "
                f"{live_expr};"
            )
        elif has_save:
            lines.append(
                f"    hwif_in_drive.{member} = "
                f"trap_enter ? {_save_port_name(reg, f)} : {live_expr};"
            )
        elif has_restore:
            lines.append(
                f"    hwif_in_drive.{member} = "
                f"xret_enter ? {_restore_port_name(reg, f)} : {live_expr};"
            )
        else:
            lines.append(f"    hwif_in_drive.{member} = {live_expr};")

    if not all_hwif:
        # Defensive: if the design has no hw_writable fields, the HwifIn
        # struct has a `_reserved` placeholder that still needs a driver.
        lines.append("    hwif_in_drive._reserved = hwif_in_live._reserved;")

    lines.append("  end comb")
    lines.append(f"end module {module_name}")
    lines.append("")
    return "\n".join(lines)
