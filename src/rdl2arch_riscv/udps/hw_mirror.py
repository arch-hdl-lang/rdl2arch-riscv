"""riscv_hw_mirror — the field's storage tracks a live external signal.

Use this on hw-writable fields whose value is a combinational mirror of
something the host system drives continuously (e.g. `mip.msip` mirroring
an incoming software-interrupt wire, `mip.mtip` mirroring the CLINT's
timer-interrupt output).

The generator emits a `mirror_<reg>_<field>: in UInt<W>` port on the
TrapCoord and, in comb, drives `hwif_in_drive.<member>` unconditionally
from that port — bypassing `hwif_in_live` for this field. Save / restore
gating doesn't apply: if the field's value is architecturally just
"whatever the source signal is this cycle", the spec's trap-entry or
xret-entry semantics aren't meaningful for it.

Validation:
  * Field must be `hw=w` or `hw=rw` (storage has to be hw-writable for
    the hwif_in drive to take effect).
  * Cannot combine with `riscv_save_on_trap` or `riscv_restore_on_ret` —
    mirror and save/restore are mutually exclusive semantics.
"""

from systemrdl.component import Field
from systemrdl.udp import UDPDefinition


class RiscvHwMirror(UDPDefinition):
    name = "riscv_hw_mirror"
    valid_components = {Field}
    valid_type = bool
    default_assignment = False
