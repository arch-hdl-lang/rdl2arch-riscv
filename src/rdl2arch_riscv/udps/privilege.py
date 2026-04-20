"""riscv_priv — minimum privilege required to access this reg / field.

Accepted on Addrmap / Regfile as well as Reg / Field so users can write
`default riscv_priv = "m";` at the top of an addrmap to propagate M-mode
to every descendant — the common case for a machine-mode CSR block.
RDL's default-resolution rules already handle propagation; we just
allow it by listing the container component types in valid_components.
"""

from systemrdl.component import Addrmap, Field, Reg, Regfile
from systemrdl.udp import UDPDefinition


class RiscvPriv(UDPDefinition):
    name = "riscv_priv"
    valid_components = {Addrmap, Regfile, Reg, Field}
    valid_type = str
    default_assignment = "m"

    def validate(self, node, value):
        if value not in ("m", "s", "u"):
            self.msg.error(
                f"riscv_priv must be one of 'm', 's', 'u' (got {value!r})",
                self.get_src_ref(node),
            )
