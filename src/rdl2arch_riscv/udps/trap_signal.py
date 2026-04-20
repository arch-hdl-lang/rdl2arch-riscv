"""riscv_trap_signal — named one-cycle pulse output when field is written.

The generator emits an output port with this name on the CSR-file module
that pulses high for one cycle on each CPU write to the tagged field. Used
to notify the pipeline about register updates that require immediate action
(e.g. trap-vector re-program triggering a fence, mstatus.MIE toggle affecting
interrupt enable).
"""

from systemrdl.component import Field, Reg
from systemrdl.udp import UDPDefinition


class RiscvTrapSignal(UDPDefinition):
    name = "riscv_trap_signal"
    valid_components = {Reg, Field}
    valid_type = str

    def validate(self, node, value):
        if not value or not value.replace("_", "").isalnum():
            self.msg.error(
                f"riscv_trap_signal {value!r} must be a non-empty identifier "
                f"(letters, digits, underscores)",
                self.get_src_ref(node),
            )
