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

    lines.append(f"end package {design.package_name}")
    lines.append("")
    return "\n".join(lines)
