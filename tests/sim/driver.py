"""Pipeline-facing driver for the CSR file + generic sim helpers.

The riscv CSR file speaks a custom pipeline interface (not AXI/APB):

  csr_addr:     UInt<12>
  csr_op:       UInt<2>     — 00=read, 01=write, 10=set, 11=clear
  csr_write_en: Bool        — granted by access controller (stubbed true here)
  csr_read_en:  Bool
  csr_wdata:    UInt<XLEN>
  csr_rdata:    UInt<XLEN>

One opcode == one cycle — the CSR file latches the effective value on the
rising edge and `csr_rdata` is comb from `csr_addr` + `csr_read_en`.
"""

from __future__ import annotations


OP_READ  = 0b00
OP_WRITE = 0b01
OP_SET   = 0b10
OP_CLEAR = 0b11


def tick(dut) -> None:
    dut.clk = 0
    dut.eval()
    dut.clk = 1
    dut.eval()


def reset(dut, cycles: int = 3) -> None:
    dut.rst = 1
    for _ in range(cycles):
        tick(dut)
    dut.rst = 0
    tick(dut)


class CsrPipelineDriver:
    """Drives the CSR file's pipeline port. Assumes access is always granted
    (test-only — real pipeline routes `granted` through from the access
    controller)."""

    def __init__(self, dut):
        self.dut = dut
        dut.csr_write_en = 0
        dut.csr_read_en = 0
        dut.csr_op = OP_READ
        dut.csr_wdata = 0
        dut.csr_addr = 0

    def _issue(self, addr: int, op: int, wdata: int, *, write: bool) -> None:
        """One-cycle CSRRW/RS/RC/RO issue: ports settle in this cycle,
        state updates on the tick."""
        self.dut.csr_addr = addr
        self.dut.csr_op = op
        self.dut.csr_wdata = wdata
        self.dut.csr_write_en = 1 if write else 0
        self.dut.csr_read_en = 0 if write else 1
        tick(self.dut)
        # Deassert after issue so subsequent ticks don't re-fire the op.
        self.dut.csr_write_en = 0
        self.dut.csr_read_en = 0

    def write(self, addr: int, data: int) -> None:
        self._issue(addr, OP_WRITE, data, write=True)

    def set(self, addr: int, data: int) -> None:
        self._issue(addr, OP_SET, data, write=True)

    def clear(self, addr: int, data: int) -> None:
        self._issue(addr, OP_CLEAR, data, write=True)

    def read(self, addr: int) -> int:
        """Combinational read: assert addr + read_en, eval_comb, latch rdata."""
        self.dut.csr_addr = addr
        self.dut.csr_op = OP_READ
        self.dut.csr_wdata = 0
        self.dut.csr_write_en = 0
        self.dut.csr_read_en = 1
        self.dut.eval_comb()
        data = self.dut.csr_rdata
        self.dut.csr_read_en = 0
        self.dut.eval_comb()
        return data
