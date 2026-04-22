"""riscv_hw_increment_when / riscv_hw_increment_high_of — counter CSRs.

A counter CSR holds a value that auto-increments each clock cycle when
a named external "enable" signal is high. SW reads the current value;
SW writes replace it (SW-write wins on the same cycle).

For 64-bit counters on RV32 (e.g. `mcycle` / `mcycleh`) the spec splits
storage into two 32-bit CSRs. The low half carries the
`riscv_hw_increment_when = "<port_name>"` UDP that names the enable
port; the high half carries `riscv_hw_increment_high_of = "<low_reg>"`
that links it to the low register. On each cycle:

  * low increments when `<port_name>` is high (and SW isn't writing).
  * high increments when `<port_name>` is high AND the low half is at
    its max value — i.e. the +1 rolls low over to zero and carries
    into high. SW can independently write either half.

Validation rules:

  * Fields carrying `riscv_hw_increment_when` or
    `riscv_hw_increment_high_of` must be sw-writable (`sw = rw`). They
    should NOT be hw-writable (`hw = w`/`hw = rw`) because the
    increment is self-driven inside the CsrFile's seq block rather
    than fed in via `hwif_in` — a hwif_in drive would fight the
    increment every cycle.

  * The register named in `riscv_hw_increment_high_of` must exist and
    contain exactly one field tagged with `riscv_hw_increment_when`
    (the counter-low field). The two fields must have the same width.

  * Counter tags are mutually exclusive with the trap-lifecycle /
    mirror tags (save_on_trap / restore_on_ret / hw_mirror).
"""

from systemrdl.component import Field
from systemrdl.udp import UDPDefinition


class RiscvHwIncrementWhen(UDPDefinition):
    name = "riscv_hw_increment_when"
    valid_components = {Field}
    valid_type = str


class RiscvHwIncrementHighOf(UDPDefinition):
    name = "riscv_hw_increment_high_of"
    valid_components = {Field}
    valid_type = str
