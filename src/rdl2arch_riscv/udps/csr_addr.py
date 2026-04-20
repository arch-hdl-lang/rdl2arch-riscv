"""riscv_csr_addr — 12-bit RISC-V CSR address for a reg.

RDL is byte-addressed; RISC-V CSRs are 12-bit register-granular. Declaring
`reg ... mstatus @ 0x300;` runs into RDL's byte-range overlap check when
two CSRs have adjacent 12-bit addresses (e.g. mcause 0x342 + mtval 0x343
both claim four bytes starting at their respective addresses).

This UDP lets users specify the CSR address directly in the RISC-V spec
numbering:

    reg {
        riscv_csr_addr = 0x300;
        field { ... } mie[3:3];
    } mstatus @ 0x0;          // RDL @ still required, but ignored

The scanner prefers `riscv_csr_addr` over the RDL byte address when both
are present. The `@` clause is still required by the systemrdl type-check
(and each reg still needs a non-overlapping byte range — space regs by
their regwidth as usual), but the byte address has no semantic role once
the UDP is set.

Without the UDP, the scanner falls back to interpreting the byte address
as `csr_addr << 2` — the pre-UDP convention. Bitwise identical results
for users who prefer that shape; the UDP exists to let RDL carry the
unshifted RISC-V spec numbers directly.
"""

from systemrdl.component import Reg
from systemrdl.udp import UDPDefinition


class RiscvCsrAddr(UDPDefinition):
    name = "riscv_csr_addr"
    valid_components = {Reg}
    valid_type = int

    def validate(self, node, value):
        if not (0 <= int(value) <= 0xFFF):
            self.msg.error(
                f"riscv_csr_addr {value:#x} is out of the 12-bit range "
                f"(0x000..0xFFF)",
                self.get_src_ref(node),
            )
