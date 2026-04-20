"""Generate a test-only integration-top ARCH module that wires the three
emitted modules (CsrFile + CsrAccess + CsrTrapCoord) into one design.

This is NOT part of the rdl2arch-riscv emitter output. It lives in tests
so we can exercise the multi-module wiring at sim level without inventing
a stable user-facing top shape yet — real SoC integration is
pipeline-specific and outside the scope of v1.

External interface (pipeline-facing):

  clk / rst                          — standard
  csr_addr / csr_opcode / csr_wdata  — CSR instruction operands
  cur_priv / valid                   — current privilege + valid signal
  trap_enter                         — one-cycle pulse to snapshot save fields
  save_<member>                      — per save-on-trap field, width-matched
  granted / illegal / cause          — access controller verdict
  csr_rdata                          — combinational readback
  <trap_signal>                      — per distinct riscv_trap_signal, pulses

Internal wiring:

  access.granted      drives  csr.csr_read_en  (any granted op reads)
  access.granted && is_write  drives  csr.csr_write_en
  trap.hwif_in_drive  drives  csr.hwif_in
  hwif_in_live        tied to zero-struct (no pipeline hw writes in tests)
"""

from __future__ import annotations

from rdl2arch_riscv.scan_csrs import CsrDesignModel


def integrated_top_name(design: CsrDesignModel) -> str:
    """`<Base>CsrFile` → `<Base>RiscvTop`."""
    base = design.module_name
    if base.endswith("CsrFile"):
        base = base[: -len("CsrFile")]
    return base + "RiscvTop"


def _save_fields(design: CsrDesignModel):
    """Every (reg_name, field_name, width) where riscv_save_on_trap is set."""
    return [
        (reg.name, f.name, f.width)
        for reg in design.regs for f in reg.fields if f.save_on_trap
    ]


def _hwif_in_members(design: CsrDesignModel):
    """Every (member_name, width) that appears in HwifIn (hw-writable fields)."""
    return [
        (f"{reg.name}_{f.name}", f.width)
        for reg in design.regs for f in reg.fields if f.hw_writable
    ]


def _trap_signals(design: CsrDesignModel) -> list[str]:
    signals = set()
    for reg in design.regs:
        if reg.trap_signal:
            signals.add(reg.trap_signal)
        for f in reg.fields:
            if f.trap_signal:
                signals.add(f.trap_signal)
    return sorted(signals)


def _module_names(design: CsrDesignModel) -> tuple[str, str, str]:
    """Mirrors exporter._sibling_name convention: <Base>CsrFile → CsrAccess,
    CsrTrapCoord."""
    base = design.module_name
    if base.endswith("CsrFile"):
        prefix = base[: -len("CsrFile")]
        return (base, prefix + "CsrAccess", prefix + "CsrTrapCoord")
    return (base, base + "CsrAccess", base + "CsrTrapCoord")


def emit_integrated_top(design: CsrDesignModel) -> str:
    """Generate the wrapper .arch source."""
    top = integrated_top_name(design)
    csr_mod, access_mod, trap_mod = _module_names(design)
    xlen = design.xlen
    hwif_in_members = _hwif_in_members(design)
    save = _save_fields(design)
    sigs = _trap_signals(design)

    lines: list[str] = []
    lines.append(f"use {design.package_name};")
    lines.append("")
    lines.append(f"module {top}")
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Sync>;")
    lines.append("")
    lines.append("  port csr_addr:   in UInt<12>;")
    lines.append("  port csr_opcode: in UInt<3>;")
    lines.append(f"  port csr_wdata:  in UInt<{xlen}>;")
    lines.append("  port cur_priv:   in UInt<2>;")
    lines.append("  port valid:      in Bool;")
    lines.append("  port trap_enter: in Bool;")
    for _reg, _fld, width in save:
        port = f"save_{_reg}_{_fld}"
        lines.append(f"  port {port}: in UInt<{width}>;")
    lines.append("")
    lines.append("  port granted:    out Bool;")
    lines.append("  port illegal:    out Bool;")
    lines.append("  port cause:      out UInt<5>;")
    lines.append(f"  port csr_rdata:  out UInt<{xlen}>;")
    for sig in sigs:
        lines.append(f"  port {sig}: out Bool;")
    lines.append("")

    # Internal wires. The CSR file's `csr_op` is UInt<2> (low 2 bits of the
    # funct3 opcode), so compute once in a wire.
    lines.append("  wire granted_w: Bool;")
    lines.append("  wire illegal_w: Bool;")
    lines.append("  wire cause_w:   UInt<5>;")
    lines.append(f"  wire rdata_w:   UInt<{xlen}>;")
    lines.append("  wire csr_op_w:  UInt<2>;")
    lines.append("  wire write_en_w: Bool;")
    lines.append(f"  wire hwif_live_w:  {design.hwif_in_struct};")
    lines.append(f"  wire hwif_drive_w: {design.hwif_in_struct};")
    lines.append(f"  wire hwif_out_w:   {design.hwif_out_struct};")
    for sig in sigs:
        lines.append(f"  wire {sig}_w: Bool;")
    lines.append("")

    # Tie hwif_in_live to all-zeros. In a real pipeline, non-trap hw writes
    # would come from here (e.g. MIE toggling on MRET); tests that don't
    # exercise that path see all-zero drives from the trap coordinator when
    # `trap_enter` is low.
    lines.append("  comb")
    for member, _w in hwif_in_members:
        lines.append(f"    hwif_live_w.{member} = 0;")
    lines.append("")
    lines.append("    csr_op_w = csr_opcode[1:0];")
    lines.append("    write_en_w = granted_w and (csr_op_w != 2'b00);")
    lines.append("")
    lines.append("    granted = granted_w;")
    lines.append("    illegal = illegal_w;")
    lines.append("    cause   = cause_w;")
    lines.append("    csr_rdata = rdata_w;")
    for sig in sigs:
        lines.append(f"    {sig} = {sig}_w;")
    lines.append("  end comb")
    lines.append("")

    # Access controller.
    lines.append(f"  inst access: {access_mod}")
    lines.append("    csr_addr   <- csr_addr;")
    lines.append("    csr_opcode <- csr_opcode;")
    lines.append("    cur_priv   <- cur_priv;")
    lines.append("    valid      <- valid;")
    lines.append("    granted    -> granted_w;")
    lines.append("    illegal    -> illegal_w;")
    lines.append("    cause      -> cause_w;")
    lines.append("  end inst access")
    lines.append("")

    # Trap coordinator.
    lines.append(f"  inst trap: {trap_mod}")
    lines.append("    clk <- clk; rst <- rst;")
    lines.append("    trap_enter <- trap_enter;")
    for _reg, _fld, _w in save:
        port = f"save_{_reg}_{_fld}"
        lines.append(f"    {port} <- {port};")
    lines.append("    hwif_in_live  <- hwif_live_w;")
    lines.append("    hwif_in_drive -> hwif_drive_w;")
    lines.append("  end inst trap")
    lines.append("")

    # CSR file.
    lines.append(f"  inst csr: {csr_mod}")
    lines.append("    clk <- clk; rst <- rst;")
    lines.append("    csr_addr     <- csr_addr;")
    lines.append("    csr_op       <- csr_op_w;")
    lines.append("    csr_write_en <- write_en_w;")
    lines.append("    csr_read_en  <- granted_w;")
    lines.append("    csr_wdata    <- csr_wdata;")
    lines.append("    csr_rdata    -> rdata_w;")
    for sig in sigs:
        lines.append(f"    {sig} -> {sig}_w;")
    lines.append("    hwif_in  <- hwif_drive_w;")
    lines.append("    hwif_out -> hwif_out_w;")
    lines.append("  end inst csr")
    lines.append("")

    lines.append(f"end module {top}")
    lines.append("")
    return "\n".join(lines)
