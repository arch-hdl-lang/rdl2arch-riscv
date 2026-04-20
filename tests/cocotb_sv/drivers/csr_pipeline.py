"""Async CSR pipeline driver for cocotb + Verilator.

Mirrors `tests/sim/driver.py` (sync/pybind) but against the
SV-instantiated integrated top. The bus consists of two handshake
channels — a valid_ready cmd (pipeline → CSR) and a valid_only rsp
(CSR → pipeline). In the current emitter `cmd_ready` is tied true and
`rsp_valid` follows `cmd_valid` combinationally, so one op == one cycle
with the rdata readable during the same cycle (pre-tick).
"""

from __future__ import annotations

from cocotb.triggers import ReadOnly, RisingEdge


# csr_opcode is funct3 — the access controller reads all 3 bits. The
# CSR file's cmd_op is derived from funct3[1:0] inside the integrated top.
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
        self.dut.csr_cmd_valid.value = 0
        self.dut.csr_cmd_addr.value = 0
        self.dut.csr_cmd_wdata.value = 0
        self.dut.csr_opcode.value = OP_NONE
        self.dut.cur_priv.value = PRIV_M
        self.dut.trap_enter.value = 0
        for name in ("save_mstatus_mpie", "save_mstatus_mpp",
                     "save_mepc_epc", "save_mcause_cause",
                     "save_mtval_tval"):
            getattr(self.dut, name).value = 0

    async def _issue(self, *, addr: int, opcode: int, wdata: int,
                     priv: int) -> None:
        self.dut.csr_cmd_addr.value = addr
        self.dut.csr_opcode.value = opcode
        self.dut.csr_cmd_wdata.value = wdata
        self.dut.cur_priv.value = priv
        self.dut.csr_cmd_valid.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.csr_cmd_valid.value = 0
        self.dut.csr_opcode.value = OP_NONE

    async def write(self, addr: int, data: int, *, priv: int = PRIV_M) -> None:
        await self._issue(addr=addr, opcode=OP_WRITE, wdata=data, priv=priv)

    async def set(self, addr: int, data: int, *, priv: int = PRIV_M) -> None:
        await self._issue(addr=addr, opcode=OP_SET, wdata=data, priv=priv)

    async def clear(self, addr: int, data: int, *, priv: int = PRIV_M) -> None:
        await self._issue(addr=addr, opcode=OP_CLEAR, wdata=data, priv=priv)

    async def read(self, addr: int, *, priv: int = PRIV_M) -> int:
        """Comb read: assert cmd_valid + OP_NONE + addr, sample rsp_rdata
        in ReadOnly before the edge."""
        self.dut.csr_cmd_addr.value = addr
        self.dut.csr_opcode.value = OP_NONE
        self.dut.csr_cmd_wdata.value = 0
        self.dut.cur_priv.value = priv
        self.dut.csr_cmd_valid.value = 1
        await ReadOnly()
        data = int(self.dut.csr_rsp_rdata.value)
        await RisingEdge(self.dut.clk)
        self.dut.csr_cmd_valid.value = 0
        return data

    async def trap_enter_snapshot(self, **saves: int) -> None:
        """Pulse `trap_enter` for one cycle with the given save_* values.
        Any save_* port not named here stays at 0 (or its last value).
        Ensures `csr_cmd_valid` is low so no incidental op fires."""
        for name, val in saves.items():
            getattr(self.dut, f"save_{name}").value = val
        self.dut.csr_cmd_valid.value = 0
        self.dut.trap_enter.value = 1
        await RisingEdge(self.dut.clk)
        self.dut.trap_enter.value = 0
