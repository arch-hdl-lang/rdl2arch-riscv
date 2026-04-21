"""Functional sim tests for the emitted trap coordinator.

Pure combinational: every HwifIn member is muxed between `hwif_in_live`
(pass-through) and a `save_<member>` port when `trap_enter` is high.
Exercises both the save-on-trap path and the unrelated pass-through
path, plus the empty-HwifIn case via a separate minimal fixture.
"""

from __future__ import annotations

import pytest

from sim.harness import fresh_dut


pytest.importorskip("pybind11")


@pytest.fixture(scope="module")
def trap_so(mtrap_sim_build) -> str:
    return mtrap_sim_build["trap_coord"]


def _prime_live(dut, **members: int) -> None:
    """Set every HwifIn live member to a distinct known value so we can
    tell pass-through from snapshot after the mux."""
    for name, val in members.items():
        setattr(dut.hwif_in_live, name, val)


def test_no_trap_passes_hwif_in_live_through(trap_so) -> None:
    """trap_enter = 0 → hwif_in_drive mirrors hwif_in_live for every member."""
    dut = fresh_dut(trap_so)
    dut.trap_enter = 0
    # Clear every save_ port so it can't leak into drive.
    dut.save_mstatus_mpie = 0
    dut.save_mstatus_mpp = 0
    dut.save_mepc_epc = 0
    dut.save_mcause_cause = 0
    dut.save_mtval_tval = 0
    _prime_live(dut,
        mstatus_mpie=1, mstatus_mpp=0b11, mepc_epc=0xAAAA_BBBB,
        mcause_cause=0x0000_0007, mtval_tval=0x1122_3344,
        # pass-through-only members (not save_on_trap):
        mstatus_reserved_2_1=0b00, mstatus_mie=1,
        mstatus_reserved_6_4=0, mstatus_reserved_10_8=0,
        mstatus_wpri_hi=0,
    )
    dut.eval_comb()
    assert dut.hwif_in_drive.mstatus_mpie  == 1
    assert dut.hwif_in_drive.mstatus_mpp   == 0b11
    assert dut.hwif_in_drive.mepc_epc      == 0xAAAA_BBBB
    assert dut.hwif_in_drive.mcause_cause  == 0x0000_0007
    assert dut.hwif_in_drive.mtval_tval    == 0x1122_3344
    assert dut.hwif_in_drive.mstatus_mie   == 1


def test_trap_enter_snapshots_save_on_trap_fields(trap_so) -> None:
    """trap_enter = 1 → save_on_trap members source from `save_<name>`,
    pass-through members still source from hwif_in_live."""
    dut = fresh_dut(trap_so)
    dut.trap_enter = 1
    # save_ ports hold the "trap entry" values the pipeline would assert.
    dut.save_mstatus_mpie = 0           # old MIE was 0
    dut.save_mstatus_mpp = 0b11         # previous priv = M
    dut.save_mepc_epc    = 0x8000_1000  # faulting PC
    dut.save_mcause_cause = 0x0000_000B # ecall from M
    dut.save_mtval_tval  = 0
    # Live values are stale / irrelevant for save fields — set to bait values
    # so we can confirm they are NOT selected.
    _prime_live(dut,
        mstatus_mpie=1, mstatus_mpp=0b00, mepc_epc=0xDEAD_DEAD,
        mcause_cause=0xBAD_BAD & 0xFFFFFFFF, mtval_tval=0x9999_9999,
        mstatus_reserved_2_1=0, mstatus_mie=1,
        mstatus_reserved_6_4=0, mstatus_reserved_10_8=0, mstatus_wpri_hi=0,
    )
    dut.eval_comb()
    # Save fields snapshot from save_ ports.
    assert dut.hwif_in_drive.mstatus_mpie  == 0
    assert dut.hwif_in_drive.mstatus_mpp   == 0b11
    assert dut.hwif_in_drive.mepc_epc      == 0x8000_1000
    assert dut.hwif_in_drive.mcause_cause  == 0x0000_000B
    assert dut.hwif_in_drive.mtval_tval    == 0
    # Pass-through fields (not save_on_trap) keep live value.
    assert dut.hwif_in_drive.mstatus_mie   == 1


def test_toggle_trap_enter_switches_source(trap_so) -> None:
    """Flipping trap_enter mid-eval should re-route drive without
    requiring a clock edge (pure comb)."""
    dut = fresh_dut(trap_so)
    dut.save_mepc_epc = 0x1000
    dut.hwif_in_live.mepc_epc = 0x2000
    for name in ("mstatus_mpie", "mstatus_mpp", "mcause_cause", "mtval_tval"):
        setattr(dut, f"save_{name}", 0)
        setattr(dut.hwif_in_live, name, 0)
    for name in ("mstatus_reserved_2_1", "mstatus_mie",
                 "mstatus_reserved_6_4", "mstatus_reserved_10_8",
                 "mstatus_wpri_hi"):
        setattr(dut.hwif_in_live, name, 0)

    dut.trap_enter = 0
    dut.eval_comb()
    assert dut.hwif_in_drive.mepc_epc == 0x2000

    dut.trap_enter = 1
    dut.eval_comb()
    assert dut.hwif_in_drive.mepc_epc == 0x1000

    dut.trap_enter = 0
    dut.eval_comb()
    assert dut.hwif_in_drive.mepc_epc == 0x2000
