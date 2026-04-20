"""Cocotb tests against the integrated-top SV — SV-level parity with the
pybind integration tests in `tests/sim/test_integration.py`.

Covers a representative subset (granted write + priv fault + trap-enter
snapshot). Full coverage at the SV level would just be duplicate
assertions; the goal here is to prove the ARCH→SV translation hasn't
diverged from the ARCH→C++ pybind path the sim tests exercise.
"""

from __future__ import annotations

import cocotb

from common import setup
from drivers.csr_pipeline import PRIV_M, PRIV_S


MSTATUS  = 0x300
MEPC     = 0x341
MSCRATCH = 0x340


@cocotb.test()
async def granted_write_then_read_roundtrips(dut):
    drv = await setup(dut)
    await drv.write(MSCRATCH, 0xA5A5_0001, priv=PRIV_M)
    val = await drv.read(MSCRATCH, priv=PRIV_M)
    assert val == 0xA5A5_0001, f"got {val:#x}"


@cocotb.test()
async def priv_fault_leaves_state_unchanged(dut):
    drv = await setup(dut)
    await drv.write(MSCRATCH, 0xDEAD_BEEF, priv=PRIV_M)
    # S-priv write attempt — access controller must deny.
    await drv.write(MSCRATCH, 0xBAD_BAD & 0xFFFFFFFF, priv=PRIV_S)
    val = await drv.read(MSCRATCH, priv=PRIV_M)
    assert val == 0xDEAD_BEEF, f"priv fault leaked write: got {val:#x}"


@cocotb.test()
async def trap_enter_snapshots_mepc(dut):
    drv = await setup(dut)
    val0 = await drv.read(MEPC, priv=PRIV_M)
    assert val0 == 0, f"mepc pre-trap should be zero, got {val0:#x}"
    await drv.trap_enter_snapshot(mepc_epc=0x8000_1000)
    val1 = await drv.read(MEPC, priv=PRIV_M)
    # mepc WARL mask 0xFFFFFFFE; 0x8000_1000 is even so it survives.
    assert val1 == 0x8000_1000, f"snapshot failed: got {val1:#x}"
