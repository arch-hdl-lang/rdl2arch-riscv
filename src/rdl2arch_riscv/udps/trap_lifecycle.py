"""riscv_save_on_trap / riscv_restore_on_ret — trap-lifecycle tags.

Fields marked `riscv_save_on_trap` are auto-written by the trap coordinator
when a trap is entered (e.g. mstatus.MIE moves to mstatus.MPIE, cur_priv
moves to mstatus.MPP, PC goes to mepc, etc.).

Fields marked `riscv_restore_on_ret` are auto-restored by the trap coordinator
when MRET / SRET / URET executes (reverse direction).

A field can carry both tags if its value round-trips through save-and-restore.
"""

from systemrdl.component import Field
from systemrdl.udp import UDPDefinition


class RiscvSaveOnTrap(UDPDefinition):
    name = "riscv_save_on_trap"
    valid_components = {Field}
    valid_type = bool
    default_assignment = False


class RiscvRestoreOnRet(UDPDefinition):
    name = "riscv_restore_on_ret"
    valid_components = {Field}
    valid_type = bool
    default_assignment = False
