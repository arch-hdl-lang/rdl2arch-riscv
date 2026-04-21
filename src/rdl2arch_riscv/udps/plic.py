"""riscv_intr_plic_role — tags a register as a PLIC role.

The RISC-V PLIC (Platform-Level Interrupt Controller) is a larger
MMIO peripheral than CLINT. Its register layout is standard across
SiFive and most RISC-V SoCs:

    0x0000 + i·4       priority[i]   — 3-bit priority for source i
                                       (source 0 is reserved / "no source")
    0x1000             pending       — N-bit bitmap, bit i = source i pending
    0x2000             enable        — N-bit bitmap, bit i = source i enabled
    0x200000           threshold     — 3-bit, only priority > threshold fires
    0x200004           claim         — read-only in v1: highest-priority
                                       pending source ID (0 = none)

The SystemRDL fixture tags each register with its role so the PLIC
emitter can pick them up. The generic `rdl2arch` emits the register
block (cpuif, decode, hwif structs); this package's `emit_plic_logic`
emits the sibling module that does priority arbitration over the
source bitmap and drives the CPU's `mip.meip` bit.

v1 scope: 1 context (M-mode), level-triggered, read-only claim (no
complete handshake), small N (8 sources). Multi-context / delegation /
edge detection / complete-semantics are deliberately deferred — each
is a follow-up that changes arbiter shape, not the register taxonomy.
"""

from systemrdl.component import Reg
from systemrdl.udp import UDPDefinition


class RiscvIntrPlicRole(UDPDefinition):
    name = "riscv_intr_plic_role"
    valid_components = {Reg}
    valid_type = str

    VALID_ROLES = {
        "priority",
        "pending",
        "enable",
        "threshold",
        "claim",
    }

    def validate(self, node, value):
        if value not in self.VALID_ROLES:
            self.msg.error(
                f"riscv_intr_plic_role {value!r} must be one of "
                f"{sorted(self.VALID_ROLES)}",
                self.get_src_ref(node),
            )
