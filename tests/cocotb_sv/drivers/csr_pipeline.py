"""Async CSR pipeline driver for cocotb + Verilator.

Mirrors `tests/sim/driver.py` (sync/pybind) but against the
SV-instantiated DUT. The integrated top is the expected interface —
the driver asserts one CSR op per cycle + current privilege, then waits
for the next rising edge.
"""

from __future__ import annotations

from cocotb.triggers import ReadOnly, RisingEdge


OP_NONE  = 0b000
OP_WRITE = 0b001
OP_SET   = 0b010
OP_CLEAR = 0b011

PRIV_U = 0b00
PRIV_S = 0b01
PRIV_M = 0b11


class CsrPipelineDriver:
    def __init__(self, dut):
        self.dut = dut

    async def init(self):
        self.dut.csr_addr.value = 0
        self.dut.csr_opcode.value = OP_NONE
        self.dut.csr_wdata.value = 0
        self.dut.cur_priv.value = PRIV_M
        self.dut.valid.value = 0
        self.dut.trap_enter.value = 0
        self.dut.save_mstatus_mpie.value = 0
        self.dut.save_mstatus_mpp.value = 0
        self.dut.save_mepc_epc.value = 0
        self.dut.save_mcause_cause.value = 0
        self.dut.save_mtval_tval.value = 0

    async def _issue(self, *, addr: int, opcode: int, wdata: int, priv: int) -> None:
        self.dut.csr_addr.value = addr
        self.dut.csr_opcode.value = opcode
        self.dut.csr_wdata.value = wdata
        self.dut.cur_priv.value = priv
        self.dut.valid.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.valid.value = 0
        self.dut.csr_opcode.value = OP_NONE

    async def write(self, addr: int, data: int, *, priv: int = PRIV_M) -> None:
        await self._issue(addr=addr, opcode=OP_WRITE, wdata=data, priv=priv)

    async def set(self, addr: int, data: int, *, priv: int = PRIV_M) -> None:
        await self._issue(addr=addr, opcode=OP_SET, wdata=data, priv=priv)

    async def clear(self, addr: int, data: int, *, priv: int = PRIV_M) -> None:
        await self._issue(addr=addr, opcode=OP_CLEAR, wdata=data, priv=priv)

    async def read(self, addr: int, *, priv: int = PRIV_M) -> int:
        """Combinational read: assert addr + valid, wait for ReadOnly phase,
        sample rdata, then deassert."""
        self.dut.csr_addr.value = addr
        self.dut.csr_opcode.value = OP_NONE
        self.dut.csr_wdata.value = 0
        self.dut.cur_priv.value = priv
        self.dut.valid.value = 1
        await ReadOnly()
        data = int(self.dut.csr_rdata.value)
        await RisingEdge(self.dut.clk)
        self.dut.valid.value = 0
        return data

    async def trap_enter_snapshot(self, **saves: int) -> None:
        """Pulse `trap_enter` for one cycle with the given save_* values.

        Any save_* port not named here stays at 0 (or its last value).
        Ensures `valid` is low so no incidental CSR op fires.
        """
        for name, val in saves.items():
            getattr(self.dut, f"save_{name}").value = val
        self.dut.valid.value = 0
        self.dut.trap_enter.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.trap_enter.value = 0
