"""riscv_wpri — reserved bit: reads zero, writes silently preserved.

Per the RISC-V privileged spec (§3.1), WPRI bits are reserved for future
standard extensions. Software should ignore them (reads return 0) and
hardware should not observe writes to them, but must not cause side
effects — functionally they're a zero constant at the read interface.
"""

from systemrdl.component import Field
from systemrdl.udp import UDPDefinition


class RiscvWpri(UDPDefinition):
    name = "riscv_wpri"
    valid_components = {Field}
    valid_type = bool
    default_assignment = False
