"""Shared helpers for rdl2arch-riscv cocotb tests."""

from __future__ import annotations

import sys
from pathlib import Path

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


_HERE = Path(__file__).resolve().parent
_PKG_ROOT = _HERE.parent
if str(_PKG_ROOT) not in sys.path:
    sys.path.insert(0, str(_PKG_ROOT))

from drivers.csr_pipeline import CsrPipelineDriver  # noqa: E402


async def setup(dut):
    """Start clock, apply reset, init driver, return driver."""
    cocotb.start_soon(Clock(dut.clk, 10, unit="ns").start())
    drv = CsrPipelineDriver(dut)
    dut.rst.value = 1
    await drv.init()
    for _ in range(3):
        await RisingEdge(dut.clk)
    dut.rst.value = 0
    await RisingEdge(dut.clk)
    return drv
