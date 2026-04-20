"""Emit the CSR access-controller ARCH module (Module 2 of the plan).

Pure combinational — takes a proposed CSR access (address + opcode + current
privilege) and produces grant / illegal + cause signals. The pipeline
forwards `granted` into the CSR file's `csr_write_en` / `csr_read_en` and
`illegal` into trap generation.

Interface:

  port csr_addr:   in  UInt<12>    — CSR address from the instruction.
  port csr_opcode: in  UInt<3>     — funct3 of the CSR instruction.
                                     Bits [1:0] select the operation:
                                       00 = not a CSR op (should be gated
                                            upstream; here it implies no write).
                                       01 = CSRRW  / CSRRWI
                                       10 = CSRRS  / CSRRSI
                                       11 = CSRRC  / CSRRCI
                                     Bit [2] selects register vs. immediate
                                     operand; the controller doesn't need it.
  port cur_priv:   in  UInt<2>     — 00=U, 01=S, 11=M (per RISC-V spec).
  port valid:      in  Bool        — asserted when csr_addr / opcode are valid.
  port granted:    out Bool        — grant to the CSR file.
  port illegal:    out Bool        — raise illegal-instruction trap.
  port cause:      out UInt<5>     — trap cause (2 = illegal instruction).

Decoding rules (privileged spec §2.1):
  min_priv := csr_addr[9:8]   — 00=U, 01=S, 10=reserved (treated as M),
                                 11=M. Per-register overrides merge via
                                 a match on csr_addr.
  is_ro    := csr_addr[11:10] == 2'b11
  is_write := csr_opcode[1:0] != 2'b00
  priv_ok  := cur_priv >= min_priv
  grant    := valid && priv_ok && !(is_ro && is_write)
"""

from .scan_csrs import CsrDesignModel


_PRIV_BITS = {"u": "2'b00", "s": "2'b01", "m": "2'b11"}


def emit_access_controller(design: CsrDesignModel, module_name: str) -> str:
    # Collect per-register priv overrides. Treat scalar priv on a reg as the
    # override; per-field priv overrides aren't wired here (they'd require a
    # per-field access check, which the plan defers — the primary lever is
    # per-reg priv).
    overrides = [
        (reg.address, _PRIV_BITS[reg.priv])
        for reg in design.regs
        if reg.priv is not None and reg.priv in _PRIV_BITS
    ]

    lines: list[str] = []
    lines.append(f"module {module_name}")
    lines.append("  port csr_addr:   in  UInt<12>;")
    lines.append("  port csr_opcode: in  UInt<3>;")
    lines.append("  port cur_priv:   in  UInt<2>;")
    lines.append("  port valid:      in  Bool;")
    lines.append("  port granted:    out Bool;")
    lines.append("  port illegal:    out Bool;")
    lines.append("  port cause:      out UInt<5>;")
    lines.append("")

    # `let` bindings live at module scope (ARCH grammar doesn't allow them
    # inside `comb`). min_priv defaults to the standard csr_addr[9:8] and is
    # overridden per-register if any riscv_priv tags were supplied.
    if overrides:
        lines.append("  let min_priv: UInt<2> = match csr_addr")
        for addr, bits in overrides:
            lines.append(f"    12'h{addr:x} => {bits},")
        lines.append("    _       => csr_addr[9:8]")
        lines.append("  end match;")
    else:
        lines.append("  let min_priv: UInt<2> = csr_addr[9:8];")

    lines.append("  let is_ro:     Bool = (csr_addr[11:10] == 2'b11);")
    lines.append("  let is_write:  Bool = csr_opcode[1:0] != 2'b00;")
    lines.append("  let priv_ok:   Bool = cur_priv >= min_priv;")
    lines.append("  let access_ok: Bool = priv_ok and not (is_ro and is_write);")
    lines.append("")
    lines.append("  comb")
    lines.append("    granted = valid and access_ok;")
    lines.append("    illegal = valid and not access_ok;")
    lines.append("    cause   = 5'd2;")
    lines.append("  end comb")
    lines.append(f"end module {module_name}")
    lines.append("")
    return "\n".join(lines)
