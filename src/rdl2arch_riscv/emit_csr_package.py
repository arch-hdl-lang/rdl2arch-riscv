"""Emit the shared ARCH package: CSR address enum, per-register structs,
hwif structs."""

from .scan_csrs import CsrDesignModel


def emit_package(design: CsrDesignModel) -> str:
    lines: list[str] = []
    lines.append(f"package {design.package_name}")
    lines.append("")

    # CSR address enum — one variant per register. The enum's job is
    # documentation + a readable match scrutinee; the actual 12-bit RISC-V
    # CSR address is still what the access check and decode key on.
    lines.append(f"  enum {design.csr_enum_name}")
    for i, reg in enumerate(design.regs):
        sep = "," if i < len(design.regs) - 1 else ""
        lines.append(f"    {reg.enum_variant}{sep}")
    lines.append(f"  end enum {design.csr_enum_name}")
    lines.append("")

    # One struct per CSR.
    for reg in design.regs:
        lines.append(f"  struct {reg.struct_name}")
        for f in reg.fields:
            lines.append(f"    {f.name}: UInt<{f.width}>;")
        lines.append(f"  end struct {reg.struct_name}")
        lines.append("")

    # Hwif in: hw-writable fields become inputs.
    in_members = [(f"{reg.name}_{f.name}", f.width)
                  for reg in design.regs for f in reg.fields if f.hw_writable]
    lines.append(f"  struct {design.hwif_in_struct}")
    if in_members:
        for name, w in in_members:
            lines.append(f"    {name}: UInt<{w}>;")
    else:
        lines.append("    _reserved: UInt<1>;")
    lines.append(f"  end struct {design.hwif_in_struct}")
    lines.append("")

    # Hwif out: hw-readable fields become outputs.
    out_members = [(f"{reg.name}_{f.name}", f.width)
                   for reg in design.regs for f in reg.fields if f.hw_readable]
    lines.append(f"  struct {design.hwif_out_struct}")
    if out_members:
        for name, w in out_members:
            lines.append(f"    {name}: UInt<{w}>;")
    else:
        lines.append("    _reserved: UInt<1>;")
    lines.append(f"  end struct {design.hwif_out_struct}")
    lines.append("")

    # Bus definition — bundles the CSR-file pipeline interface with two
    # handshake channels. Lives inside the package (arch-com >= 0.44
    # supports nested `bus`) so the interface type groups with the data
    # types that define the same pipeline boundary.
    #
    #   cmd (initiator → target, valid_ready): the pipeline requests a CSR
    #     access. Target asserts cmd_ready to accept; handshake fires on
    #     (cmd_valid && cmd_ready). Payload is addr/op/wdata.
    #
    #   rsp (target → initiator, valid_only): the CSR file returns rdata.
    #     No ready because the pipeline is always assumed ready to consume
    #     its CSR op result this cycle. Upgrade to valid_ready here if a
    #     future downstream wants to backpressure.
    #
    # Directions are written from the initiator's (pipeline's) perspective.
    #
    # The access controller deliberately keeps flat ports: it produces
    # `granted` as a flat output so the integrated top (or any wrapper)
    # can consume it with a scalar wire alongside the handshake — the
    # bus-wire support in arch >= 0.44 gives us room to simplify that
    # further, but the current shape is what the existing tests expect.
    xlen = design.xlen
    lines.append(f"  bus {design.csr_file_bus}")
    lines.append("    handshake cmd: send kind: valid_ready")
    lines.append("      addr:  UInt<12>;")
    lines.append("      op:    UInt<2>;")
    lines.append(f"      wdata: UInt<{xlen}>;")
    lines.append("    end handshake cmd")
    lines.append("")
    lines.append("    handshake rsp: receive kind: valid_only")
    lines.append(f"      rdata: UInt<{xlen}>;")
    lines.append("    end handshake rsp")
    lines.append(f"  end bus {design.csr_file_bus}")
    lines.append("")

    lines.append(f"end package {design.package_name}")
    lines.append("")

    return "\n".join(lines)
