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
  xret_enter                         — one-cycle pulse to apply restore fields
  save_<member>                      — per save-on-trap field, width-matched
  restore_<member>                   — per restore-on-ret field, width-matched
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


def _restore_fields(design: CsrDesignModel):
    """Every (reg_name, field_name, width) where riscv_restore_on_ret is set."""
    return [
        (reg.name, f.name, f.width)
        for reg in design.regs for f in reg.fields if f.restore_on_ret
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
    restore = _restore_fields(design)
    sigs = _trap_signals(design)

    lines: list[str] = []
    lines.append(f"use {design.package_name};")
    lines.append("")
    lines.append(f"module {top}")
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Sync>;")
    lines.append("")
    # Pipeline-facing cmd handshake (flat for test driveability — the
    # downstream CSR file's bus port is bound per-field from these).
    # `csr_cmd_op` is derived from `csr_opcode[1:0]` inside this top, so
    # the pipeline only needs to drive one opcode signal.
    lines.append("  port csr_cmd_valid: in  Bool;")
    lines.append("  port csr_cmd_ready: out Bool;")
    lines.append("  port csr_cmd_addr:  in  UInt<12>;")
    lines.append(f"  port csr_cmd_wdata: in  UInt<{xlen}>;")
    lines.append("  port csr_rsp_valid: out Bool;")
    lines.append(f"  port csr_rsp_rdata: out UInt<{xlen}>;")
    # The access controller wants all 3 bits of funct3 to distinguish
    # r/w/rs/rc.
    lines.append("  port csr_opcode: in  UInt<3>;")
    lines.append("  port cur_priv:   in  UInt<2>;")
    lines.append("  port trap_enter: in  Bool;")
    lines.append("  port xret_enter: in  Bool;")
    for _reg, _fld, width in save:
        port = f"save_{_reg}_{_fld}"
        lines.append(f"  port {port}: in UInt<{width}>;")
    for _reg, _fld, width in restore:
        port = f"restore_{_reg}_{_fld}"
        lines.append(f"  port {port}: in UInt<{width}>;")
    lines.append("")
    lines.append("  port granted:    out Bool;")
    lines.append("  port illegal:    out Bool;")
    lines.append("  port cause:      out UInt<5>;")
    for sig in sigs:
        lines.append(f"  port {sig}: out Bool;")
    lines.append("")

    # Internal wires. All flat — ARCH only permits bus types on ports, not
    # on `wire` declarations. The CSR file's bus port is bound per-field
    # inside its `inst` block.
    lines.append("  wire granted_w: Bool;")
    lines.append("  wire illegal_w: Bool;")
    lines.append("  wire cause_w:   UInt<5>;")
    lines.append("  wire cmd_ready_w: Bool;")
    lines.append("  wire cmd_op_w:   UInt<2>;")
    lines.append("  wire rsp_valid_w: Bool;")
    lines.append(f"  wire rsp_rdata_w: UInt<{xlen}>;")
    lines.append(f"  wire hwif_live_w:  {design.hwif_in_struct};")
    lines.append(f"  wire hwif_drive_w: {design.hwif_in_struct};")
    lines.append(f"  wire hwif_out_w:   {design.hwif_out_struct};")
    for sig in sigs:
        lines.append(f"  wire {sig}_w: Bool;")
    lines.append("")

    # Tie hwif_in_live to all-zeros and fan internal wires back to flat
    # top-level outputs.
    lines.append("  comb")
    for member, _w in hwif_in_members:
        lines.append(f"    hwif_live_w.{member} = 0;")
    lines.append("")
    lines.append("    cmd_op_w = csr_opcode[1:0];")
    lines.append("    csr_cmd_ready = cmd_ready_w;")
    lines.append("    csr_rsp_valid = rsp_valid_w;")
    lines.append("    csr_rsp_rdata = rsp_rdata_w;")
    lines.append("    granted = granted_w;")
    lines.append("    illegal = illegal_w;")
    lines.append("    cause   = cause_w;")
    for sig in sigs:
        lines.append(f"    {sig} = {sig}_w;")
    lines.append("  end comb")
    lines.append("")

    # Access controller (flat ports).
    lines.append(f"  inst access: {access_mod}")
    lines.append("    csr_addr   <- csr_cmd_addr;")
    lines.append("    csr_opcode <- csr_opcode;")
    lines.append("    cur_priv   <- cur_priv;")
    lines.append("    valid      <- csr_cmd_valid;")
    lines.append("    granted    -> granted_w;")
    lines.append("    illegal    -> illegal_w;")
    lines.append("    cause      -> cause_w;")
    lines.append("  end inst access")
    lines.append("")

    # Trap coordinator.
    lines.append(f"  inst trap: {trap_mod}")
    lines.append("    clk <- clk; rst <- rst;")
    lines.append("    trap_enter <- trap_enter;")
    lines.append("    xret_enter <- xret_enter;")
    for _reg, _fld, _w in save:
        port = f"save_{_reg}_{_fld}"
        lines.append(f"    {port} <- {port};")
    for _reg, _fld, _w in restore:
        port = f"restore_{_reg}_{_fld}"
        lines.append(f"    {port} <- {port};")
    lines.append("    hwif_in_live  <- hwif_live_w;")
    lines.append("    hwif_in_drive -> hwif_drive_w;")
    lines.append("  end inst trap")
    lines.append("")

    # CSR file — bus port bound per-field. The `granted` input comes
    # from the access controller's flat output. Individual field binding
    # lets the top interpose on handshake signals if needed.
    lines.append(f"  inst csr: {csr_mod}")
    lines.append("    clk <- clk; rst <- rst;")
    lines.append("    csr.cmd_valid <- csr_cmd_valid;")
    lines.append("    csr.cmd_ready -> cmd_ready_w;")
    lines.append("    csr.cmd_addr  <- csr_cmd_addr;")
    lines.append("    csr.cmd_op    <- cmd_op_w;")
    lines.append("    csr.cmd_wdata <- csr_cmd_wdata;")
    lines.append("    csr.rsp_valid -> rsp_valid_w;")
    lines.append("    csr.rsp_rdata -> rsp_rdata_w;")
    lines.append("    granted <- granted_w;")
    for sig in sigs:
        lines.append(f"    {sig} -> {sig}_w;")
    lines.append("    hwif_in  <- hwif_drive_w;")
    lines.append("    hwif_out -> hwif_out_w;")
    lines.append("  end inst csr")
    lines.append("")

    lines.append(f"end module {top}")
    lines.append("")
    return "\n".join(lines)
