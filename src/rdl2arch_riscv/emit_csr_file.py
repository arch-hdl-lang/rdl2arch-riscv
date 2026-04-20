"""Emit the top CSR-file ARCH module.

Pipeline-facing interface is a `target <Name>CsrFileBus` port. The bus
bundles the CSR access signals; directions below are from the
pipeline's (initiator's) perspective:

  csr.addr:     out UInt<12>
  csr.write_en: out Bool      — granted by the access controller
  csr.read_en:  out Bool      — granted by the access controller
  csr.op:       out UInt<2>   — 00=read-only, 01=write, 10=set, 11=clear
  csr.wdata:    out UInt<XLEN>
  csr.rdata:    in  UInt<XLEN>

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
        return f"csr.wdata[{field.lsb}]"
    return f"csr.wdata[{field.msb}:{field.lsb}]"


def _reset_struct_literal(reg: CsrRegModel) -> str:
    parts = ", ".join(f"{f.name}: {f.reset}" for f in reg.fields)
    return f"{reg.struct_name} {{ {parts} }}"


def _opcode_match_lines(field: CsrFieldModel, old_ref: str) -> list[str]:
    """Per-opcode new value as a multi-line match block (no trailing semicolon).

    Caller splices the lines in with appropriate indentation.
    """
    slice_expr = _wdata_slice(field)
    return [
        "match csr.op",
        f"  2'b01 => {slice_expr},",
        f"  2'b10 => {old_ref} | {slice_expr},",
        f"  2'b11 => {old_ref} & (~{slice_expr}),",
        f"  _    => {old_ref}",
        "end match",
    ]


def _field_write_lines(field: CsrFieldModel, state_ref: str) -> list[str]:
    """ARCH seq lines for a CPU write to this field, or [] if nothing to emit.

    Returned lines are indentation-agnostic — the caller prefixes them with
    the right leading whitespace. The first line starts the assignment;
    subsequent lines are continuations that need an extra indent.
    """
    if field.wpri or not field.sw_writable:
        # WPRI fields silently discard writes — no statement needed at all.
        # Non-sw-writable fields shouldn't reach here in the first place, but
        # guard just in case.
        return []

    lhs = f"{state_ref}.{field.name}"
    old_ref = lhs
    op_lines = _opcode_match_lines(field, old_ref)

    # Plain (no WARL): `lhs <= match csr_op ... end match;`
    if field.warl is None:
        out = [f"{lhs} <= {op_lines[0]}"]
        out.extend(op_lines[1:-1])
        out.append(f"{op_lines[-1]};")
        return out

    kind, payload = field.warl

    # WARL bitmask: `lhs <= (match csr_op ... end match) & mask;`
    if kind == "mask":
        mask_lit = f"{field.width}'h{int(payload) & ((1 << field.width) - 1):x}"
        out = [f"{lhs} <= ({op_lines[0]}"]
        out.extend(op_lines[1:-1])
        out.append(f"{op_lines[-1]}) & {mask_lit};")
        return out

    # WARL enum-list: outer match against the legal values; default = legal[0].
    if kind == "enum":
        legal: list[int] = sorted(set(payload))
        default = f"{field.width}'h{legal[0]:x}"
        out = [f"{lhs} <= match ({op_lines[0]}"]
        out.extend(op_lines[1:-1])
        out.append(f"{op_lines[-1]})")
        for v in legal:
            lit = f"{field.width}'h{v:x}"
            out.append(f"  {lit} => {lit},")
        out.append(f"  _    => {default}")
        out.append("end match;")
        return out

    # Unknown warl kind: preserve conservatively by emitting nothing.
    return []


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
    lines.append("  port clk: in Clock<SysDomain>;")
    lines.append("  port rst: in Reset<Sync>;")
    lines.append(f"  port csr: target {design.csr_file_bus};")

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

    # Combinational readback mux: match on csr.addr against each CSR's 12-bit
    # RISC-V address; value is the packed read-view of the register.
    lines.append(f"  let csr_rdata_mux: UInt<{xlen}> = match csr.addr")
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

    # Per-register write block. Collect lines per-reg, filtering out regs
    # whose only fields are WPRI or non-sw-writable (nothing to emit).
    #
    # Each item is (reg, [line, ...]) where the lines already include the
    # leading `<state>.<field> <= ...;` and any trap-signal pulse assigns,
    # but NOT the surrounding `if csr_addr == ... end if`. The caller wraps
    # those + applies address-block indentation.
    per_reg_writes: list[tuple[CsrRegModel, list[str]]] = []
    for reg in design.regs:
        block: list[str] = []
        for f in reg.fields:
            fl = _field_write_lines(f, reg.state_name)
            if fl:
                block.extend(fl)
                if f.trap_signal:
                    block.append(f"{f.trap_signal}_r <= true;")
        if reg.trap_signal:
            block.append(f"{reg.trap_signal}_r <= true;")
        if block:
            per_reg_writes.append((reg, block))

    if per_reg_writes:
        lines.append("    if csr.write_en")
        for reg, block in per_reg_writes:
            lines.append(f"      if csr.addr == 12'h{reg.address:x}")
            # First line of each statement starts at 8-space indent; its
            # continuation lines go 2 more in (10 spaces). Each top-level
            # statement in `block` either begins with `<lhs> <=` (start of a
            # new assign) or `<signal>_r <= true;` (atomic one-liner). We
            # detect statement starts by whether the line contains ` <= `
            # at the top (the `match csr_op` et al continuation lines don't).
            stmt_indent = " " * 8
            cont_indent = " " * 10
            for ln in block:
                if " <= " in ln and not ln.startswith(" "):
                    lines.append(stmt_indent + ln)
                elif ln.startswith("end match"):
                    lines.append(cont_indent + ln)
                else:
                    lines.append(cont_indent + ln)
            lines.append(f"      end if")
        lines.append("    end if")

    lines.append("  end seq")
    lines.append("")

    # Comb block: drive csr.rdata, trap-signal pulses, hwif_out.
    lines.append("  comb")
    lines.append("    csr.rdata = csr.read_en ? csr_rdata_mux : 0;")
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
