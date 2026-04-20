"""Pipeline-facing driver for the CSR file + generic sim helpers.

The riscv CSR file speaks a two-handshake bus (ARCH `bus` + `handshake`
primitives):

  cmd (initiator → target, valid_ready)
    cmd_valid / cmd_ready
    cmd_addr  : UInt<12>
    cmd_op    : UInt<2>   — 00=read-only, 01=write, 10=set, 11=clear
    cmd_wdata : UInt<XLEN>

  rsp (target → initiator, valid_only)
    rsp_valid
    rsp_rdata : UInt<XLEN>

Plus a flat `granted: in Bool` input that the access controller drives
at the system level — writes only land on state when `granted` is high.

In this emitter `cmd_ready` is tied true (1-cycle accept), so the driver
doesn't need to wait for ready. `rsp_valid`/`rsp_rdata` follow
`cmd_valid` combinationally — reads are pure comb, writes latch on the
next rising edge.
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
    """Drives the CSR file's pipeline bus. For the isolated CSR-file sim,
    `granted` is a direct input on the DUT; for the integrated top it's
    driven internally by the access controller and the caller can leave
    the `granted` kwarg at its default."""

    def __init__(self, dut):
        self.dut = dut
        dut.csr_cmd_valid = 0
        dut.csr_cmd_op = OP_READ
        dut.csr_cmd_addr = 0
        dut.csr_cmd_wdata = 0
        if hasattr(dut, "granted"):
            dut.granted = 0

    def _issue_tick(self, *, addr: int, op: int, wdata: int, granted: bool) -> None:
        """Assert a cmd for one cycle; tick latches state on writes."""
        self.dut.csr_cmd_addr = addr
        self.dut.csr_cmd_op = op
        self.dut.csr_cmd_wdata = wdata
        self.dut.csr_cmd_valid = 1
        if hasattr(self.dut, "granted"):
            self.dut.granted = 1 if granted else 0
        tick(self.dut)
        self.dut.csr_cmd_valid = 0

    def write(self, addr: int, data: int, *, granted: bool = True) -> None:
        self._issue_tick(addr=addr, op=OP_WRITE, wdata=data, granted=granted)

    def set(self, addr: int, data: int, *, granted: bool = True) -> None:
        self._issue_tick(addr=addr, op=OP_SET, wdata=data, granted=granted)

    def clear(self, addr: int, data: int, *, granted: bool = True) -> None:
        self._issue_tick(addr=addr, op=OP_CLEAR, wdata=data, granted=granted)

    def read(self, addr: int, *, granted: bool = True) -> int:
        """Comb read. Asserts cmd_valid + cmd_op=00 + addr, samples
        `csr_rsp_rdata` without a clock edge."""
        self.dut.csr_cmd_addr = addr
        self.dut.csr_cmd_op = OP_READ
        self.dut.csr_cmd_wdata = 0
        self.dut.csr_cmd_valid = 1
        if hasattr(self.dut, "granted"):
            self.dut.granted = 1 if granted else 0
        self.dut.eval_comb()
        data = self.dut.csr_rsp_rdata
        self.dut.csr_cmd_valid = 0
        self.dut.eval_comb()
        return data
