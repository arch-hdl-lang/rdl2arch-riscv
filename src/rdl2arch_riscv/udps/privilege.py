"""riscv_priv — minimum privilege required to access this reg / field."""

from systemrdl.component import Field, Reg
from systemrdl.udp import UDPDefinition


class RiscvPriv(UDPDefinition):
    name = "riscv_priv"
    valid_components = {Reg, Field}
    valid_type = str
    default_assignment = "m"

    def validate(self, node, value):
        if value not in ("m", "s", "u"):
            self.msg.error(
                f"riscv_priv must be one of 'm', 's', 'u' (got {value!r})",
                self.get_src_ref(node),
            )
