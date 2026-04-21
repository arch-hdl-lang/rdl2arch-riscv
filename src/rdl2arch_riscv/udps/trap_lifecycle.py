"""riscv_save_on_trap / riscv_restore_on_ret — trap-lifecycle tags.

The generator emits one port per tagged field on `<Name>CsrTrapCoord`:

  riscv_save_on_trap    → `save_<reg>_<field>: in UInt<W>`     — sampled
                          into the CSR file on `trap_enter`.
  riscv_restore_on_ret  → `restore_<reg>_<field>: in UInt<W>`  — sampled
                          into the CSR file on `xret_enter`.

The pipeline supplies the data. For a standard RISC-V M-mode core that's
typically:

  save path   (trap entry):    save_mstatus_mpie  <- old mstatus.mie
                               save_mstatus_mpp   <- priv_lvl_q
                               save_mepc_epc      <- pc_of_faulting_instr
                               save_mcause_cause  <- encoded_cause
                               save_mtval_tval    <- faulting_address_or_0
  restore path (mret):         restore_mstatus_mie  <- hwif_out.mstatus_mpie
                               restore_mstatus_mpie <- 1
                               restore_mstatus_mpp  <- U (or M on M-only cores)

A field can carry both tags if its value round-trips through save-and-restore
(e.g. `mstatus.mpie` — saved on trap entry, restored on mret). The generator
emits priority `trap_enter > xret_enter > hwif_in_live` in that case; the
two pulses are specified to be mutually exclusive by the privileged spec,
so the order is a safety belt rather than a semantic knob.
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
