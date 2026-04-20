"""Emit the top CSR-file ARCH module.

Port convention (custom, not a bus — CSR accesses come from the pipeline):

  port csr_addr:     in  UInt<12>
  port csr_write_en: in  Bool      — granted by the access controller
  port csr_read_en:  in  Bool      — granted by the access controller
  port csr_op:       in  UInt<2>   — 00=read-only, 01=write, 10=set, 11=clear
  port csr_wdata:    in  UInt<XLEN>
  port csr_rdata:    out UInt<XLEN>

Plus one `<signal>_pulse: out Bool` port per distinct `riscv_trap_signal`
value and hwif_in/hwif_out structs for hw-driven fields.

Write-side effective-value computation per opcode:
  WRITE:  new = wdata
  SET:    new = old | wdata
  CLEAR:  new = old & ~wdata

WPRI fields: not sw-writable (held at 0), not readable (readback masked
to 0 for that slice). WARL fields: the effective-value is further
coerced before being latched. Bitmask form: `new &= mask`. Enum-list
form: `new` is coerced to the largest listed value ≤ new, else the
minimum listed value.
"""

from .scan_csrs import CsrDesignModel, CsrFieldModel, CsrRegModel


def _indent(text: str, n: int) -> str:
    pad = " " * n
    return "\n".join(pad + ln if ln else ln for ln in text.splitlines())


def _zero_lit(w: int) -> str:
    return "false" if w == 1 else f"{w}'h0"


def _ones_lit(w: int) -> str:
    if w == 1:
        return "true"
    return f"{w}'h{(1 << w) - 1:x}"


def _wdata_slice(field: CsrFieldModel) -> str:
    """Extract `wdata[msb:lsb]` to get this field's new-value slice."""
    if field.width == 1:
        return f"csr_wdata[{field.lsb}]"
    return f"csr_wdata[{field.msb}:{field.lsb}]"


def _reset_struct_literal(reg: CsrRegModel) -> str:
    parts = ", ".join(f"{f.name}: {f.reset}" for f in reg.fields)
    return f"{reg.struct_name} {{ {parts} }}"


def _field_new_value(field: CsrFieldModel, old_ref: str) -> str:
    """The effective new value for a field after opcode + WPRI/WARL coercion.

    `old_ref` is the ARCH expression for the field's current value (e.g.
    `mstatus_r.mie`). Returns an ARCH expression of the same width.
    """
    slice_expr = _wdata_slice(field)

    # Opcode-dependent raw new value: we compute the operand-applied value,
    # then apply WPRI / WARL coercion.
    op_expr = (
        f"(match csr_op "
        f"2'b01 => {slice_expr}, "
        f"2'b10 => {old_ref} | {slice_expr}, "
        f"2'b11 => {old_ref} & (~{slice_expr}), "
        f"_ => {old_ref} "
        f"end match)"
    )

    # WPRI: field is reserved, drop any software write.
    if field.wpri:
        return old_ref

    # WARL: legalize the operand-applied value.
    if field.warl is not None:
        kind, payload = field.warl
        if kind == "mask":
            mask_lit = f"{field.width}'h{int(payload) & ((1 << field.width) - 1):x}"
            return f"({op_expr} & {mask_lit})"
        if kind == "enum":
            legal: list[int] = sorted(set(payload))
            # Nested match: if op_expr == legal_i, return legal_i; else ...
            # Simplification: the operand-applied value either equals one of
            # the legal values (keep it), or falls back to the first listed
            # value. Implementation: a match scrutinee on op_expr against each
            # legal value; default branch returns legal[0].
            arms = ",\n    ".join(
                f"{field.width}'h{v:x} => {field.width}'h{v:x}"
                for v in legal
            )
            default = f"{field.width}'h{legal[0]:x}"
            return (
                f"match {op_expr}\n"
                f"    {arms},\n"
                f"    _ => {default}\n"
                f"  end match"
            )
        # Unknown warl kind: preserve old value conservatively.
        return old_ref

    # Plain sw-writable field: opcode-applied value.
    return op_expr


def _field_read_value(field: CsrFieldModel, state_ref: str) -> str:
    """Value seen by software on a read. WPRI masks to zero; otherwise
    return the stored field value.
    """
    if field.wpri or not field.sw_readable:
        return _zero_lit(field.width)
    return f"{state_ref}.{field.name}"


def emit_csr_file(design: CsrDesignModel) -> str:
    lines: list[str] = []
    xlen = design.xlen
    lines.append(f"use {design.package_name};")
    lines.append("")
    lines.append(f"module {design.module_name}")
    lines.append("  port clk:          in Clock<SysDomain>;")
    lines.append("  port rst:          in Reset<Sync>;")
    lines.append("  port csr_addr:     in UInt<12>;")
    lines.append("  port csr_write_en: in Bool;")
    lines.append("  port csr_read_en:  in Bool;")
    lines.append("  port csr_op:       in UInt<2>;")
    lines.append(f"  port csr_wdata:    in UInt<{xlen}>;")
    lines.append(f"  port csr_rdata:    out UInt<{xlen}>;")

    # One named-pulse output per distinct riscv_trap_signal value.
    trap_signals = sorted({
        sig for reg in design.regs for sig in _all_trap_signals(reg)
    })
    for sig in trap_signals:
        lines.append(f"  port {sig}: out Bool;")

    lines.append(f"  port hwif_in:  in {design.hwif_in_struct};")
    lines.append(f"  port hwif_out: out {design.hwif_out_struct};")
    lines.append("")
    lines.append("  default seq on clk rising;")
    lines.append("")

    # State declarations.
    for reg in design.regs:
        lines.append(
            f"  reg {reg.state_name}: {reg.struct_name} "
            f"reset rst => {_reset_struct_literal(reg)};"
        )
    lines.append("")

    # Trap-signal pulse regs — one-cycle-high registers.
    for sig in trap_signals:
        lines.append(f"  reg {sig}_r: Bool reset rst => false;")
    lines.append("")

    # Combinational readback mux: match on csr_addr against each CSR's 12-bit
    # RISC-V address; value is the packed read-view of the register.
    lines.append(f"  let csr_rdata_mux: UInt<{xlen}> = match csr_addr")
    for reg in design.regs:
        expr = _reg_read_expr(reg, xlen)
        lines.append(f"    12'h{reg.address:x} => {expr},")
    lines.append("    _ => 0")
    lines.append("  end match;")
    lines.append("")

    # Seq block: writes (per-reg address decode), hwif_in drives, trap-signal
    # pulse maintenance.
    lines.append("  seq")

    # Default: trap-signal pulses reset each cycle unless re-asserted below.
    for sig in trap_signals:
        lines.append(f"    {sig}_r <= false;")

    # hwif_in -> reg-state (continuous).
    for reg in design.regs:
        for f in reg.fields:
            if f.hw_writable:
                lines.append(
                    f"    {reg.state_name}.{f.name} <= hwif_in.{reg.name}_{f.name};"
                )

    # Per-register write block.
    per_reg_writes: list[tuple[CsrRegModel, list[str]]] = []
    for reg in design.regs:
        stmts: list[str] = []
        reg_trap_sig = reg.trap_signal
        for f in reg.fields:
            if not f.sw_writable:
                continue
            new_val = _field_new_value(f, f"{reg.state_name}.{f.name}")
            stmts.append(f"{reg.state_name}.{f.name} <= {new_val};")
            if f.trap_signal:
                stmts.append(f"{f.trap_signal}_r <= true;")
        if reg_trap_sig:
            stmts.append(f"{reg_trap_sig}_r <= true;")
        if stmts:
            per_reg_writes.append((reg, stmts))

    if per_reg_writes:
        lines.append("    if csr_write_en")
        for reg, stmts in per_reg_writes:
            lines.append(f"      if csr_addr == 12'h{reg.address:x}")
            for stmt in stmts:
                # Multi-line `new_value` (nested match) — indent continuation.
                for i, ln in enumerate(stmt.splitlines()):
                    pad = "        " if i == 0 else "          "
                    lines.append(pad + ln)
            lines.append("      end if")
        lines.append("    end if")

    lines.append("  end seq")
    lines.append("")

    # Comb block: drive csr_rdata, trap-signal pulses, hwif_out.
    lines.append("  comb")
    lines.append("    csr_rdata = csr_read_en ? csr_rdata_mux : 0;")
    for sig in trap_signals:
        lines.append(f"    {sig} = {sig}_r;")
    for reg in design.regs:
        for f in reg.fields:
            if f.hw_readable:
                lines.append(
                    f"    hwif_out.{reg.name}_{f.name} = "
                    f"{reg.state_name}.{f.name};"
                )
    lines.append("  end comb")
    lines.append("")
    lines.append(f"end module {design.module_name}")
    lines.append("")
    return "\n".join(lines)


def _reg_read_expr(reg: CsrRegModel, xlen: int) -> str:
    """Compose the packed read-view of one CSR. Pads to xlen with zeros."""
    if not reg.fields:
        return _zero_lit(xlen)
    fields_sorted = sorted(reg.fields, key=lambda f: f.lsb, reverse=True)
    parts: list[str] = []
    next_bit = reg.regwidth - 1
    for f in fields_sorted:
        if f.msb < next_bit:
            parts.append(f"{next_bit - f.msb}'h0")
        parts.append(_field_read_value(f, reg.state_name))
        next_bit = f.lsb - 1
    if next_bit >= 0:
        parts.append(f"{next_bit + 1}'h0")
    body = parts[0] if len(parts) == 1 else "{" + ", ".join(parts) + "}"
    if reg.regwidth < xlen:
        pad = xlen - reg.regwidth
        return "{" + f"{pad}'h0, {body}" + "}"
    return body


def _all_trap_signals(reg: CsrRegModel):
    if reg.trap_signal:
        yield reg.trap_signal
    for f in reg.fields:
        if f.trap_signal:
            yield f.trap_signal
