"""riscv_intr_clint_role — tags a register as a CLINT role.

The RISC-V CLINT (Core-Local INTerrupt controller) is a tiny MMIO
peripheral that drives two of the CPU's `mip` bits: `mip.msip` from a
software-writable flip-flop and `mip.mtip` from a 64-bit timer
comparator. Its register layout is fixed by convention (SiFive FU540
+ later, followed by virtually every RISC-V SoC):

    0x0000 + hart·4   msip[hart]        — 32-bit, bit 0 is the pending bit
    0x4000 + hart·8   mtimecmp_lo[hart] — low 32 bits of 64-bit comparator
    0x4004 + hart·8   mtimecmp_hi[hart]
    0xBFF8            mtime_lo          — low 32 bits of shared 64-bit counter
    0xBFFC            mtime_hi

SystemRDL doesn't know what these registers mean. This UDP is how
the CLINT emitter recognizes each reg and wires up the corresponding
bit of hardware:

    reg {
        riscv_intr_clint_role = "msip";
        field { sw = rw; hw = r; reset = 0; } value[0:0];
        field { sw = r;  hw = r; reset = 0; } reserved[31:1];
    } msip @ 0x0000;

The generic `rdl2arch` still emits the register block (address decode,
hwif struct, cpuif wiring); the extra logic — the timer increment,
the `mtime >= mtimecmp` comparator, the bit-0 extract of msip — is
emitted as a sibling `ClintLogic` module.
"""

from systemrdl.component import Reg
from systemrdl.udp import UDPDefinition


class RiscvIntrClintRole(UDPDefinition):
    name = "riscv_intr_clint_role"
    valid_components = {Reg}
    valid_type = str

    VALID_ROLES = {
        "msip",
        "mtimecmp_lo",
        "mtimecmp_hi",
        "mtime_lo",
        "mtime_hi",
    }

    def validate(self, node, value):
        if value not in self.VALID_ROLES:
            self.msg.error(
                f"riscv_intr_clint_role {value!r} must be one of "
                f"{sorted(self.VALID_ROLES)}",
                self.get_src_ref(node),
            )
