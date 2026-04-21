"""Functional sim tests for the emitted PLIC logic module.

Builds `PlicLogic` via `arch sim --pybind` and exercises the
arbiter's priority/enable/threshold/tiebreak behavior against the
8-source, 1-context fixture.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import RDL_DIR
from sim.driver import reset, tick
from sim.harness import fresh_dut


pytest.importorskip("pybind11")


@pytest.fixture(scope="session")
def plic_so(arch_bin, tmp_path_factory) -> str:
    from rdl2arch_riscv import RiscvPlicExporter
    from rdl2arch_riscv.udps import ALL_UDPS
    from systemrdl import RDLCompiler

    out = tmp_path_factory.mktemp("plic_sim")
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(RDL_DIR / "plic_basic.rdl"))
    RiscvPlicExporter().export(rdlc.elaborate().top, str(out))

    build_dir = out / "sim"
    arch_files = sorted(out.glob("*.arch"))
    result = subprocess.run(
        [arch_bin, "sim", "--pybind", "-o", str(build_dir),
         *[str(p) for p in arch_files]],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"arch sim --pybind failed:\nSTDERR:\n{result.stderr}"
        )
    so = list(build_dir.glob("VPlicLogic_pybind.*.so"))
    assert so, f"PlicLogic .so missing in {build_dir}"
    return str(so[0])


def _drive(dut, *, sources: int, priorities: dict[int, int],
           enables: dict[int, bool], threshold: int) -> tuple[int, int]:
    """Set PLIC state, evaluate, return (winner_id, intr_out_bit0).

    `intr_out` is a UInt<N_contexts> bitmap; bit 0 is context 0 (M-mode)
    — the one this fixture declares. For multi-context tests see
    `test_plic_multictx.py`.
    """
    dut.source_in = sources
    for i in range(9):
        setattr(dut.hwif_out, f"priority_{i}_value", priorities.get(i, 0))
    dut.hwif_out.enable_0_value = sum(
        (1 << i) if enables.get(i, False) else 0 for i in range(9)
    )
    dut.hwif_out.threshold_0_value = threshold
    dut.eval_comb()
    return int(dut.hwif_in.claim_0_value), int(dut.intr_out) & 1


def test_plic_no_source_gives_no_winner(plic_so) -> None:
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut, sources=0, priorities={}, enables={}, threshold=0)
    assert winner == 0 and meip == 0


def test_plic_single_source_fires(plic_so) -> None:
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut,
        sources=(1 << 3),
        priorities={3: 5},
        enables={3: True},
        threshold=0,
    )
    assert winner == 3 and meip == 1


def test_plic_threshold_blocks_equal_priority(plic_so) -> None:
    """priority > threshold is strict; priority == threshold is blocked."""
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut,
        sources=(1 << 3),
        priorities={3: 5},
        enables={3: True},
        threshold=5,
    )
    assert winner == 0 and meip == 0


def test_plic_disabled_source_blocked(plic_so) -> None:
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut,
        sources=(1 << 3),
        priorities={3: 5},
        enables={3: False},
        threshold=0,
    )
    assert winner == 0 and meip == 0


def test_plic_equal_priority_tiebreaks_to_lowest_id(plic_so) -> None:
    """Sources 2 and 5 both pending+enabled at prio 3 → source 2 wins."""
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut,
        sources=(1 << 2) | (1 << 5),
        priorities={2: 3, 5: 3},
        enables={2: True, 5: True},
        threshold=0,
    )
    assert winner == 2 and meip == 1


def test_plic_higher_priority_wins_over_lower_id(plic_so) -> None:
    """Source 5 (prio 7) beats source 2 (prio 3) despite higher ID."""
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut,
        sources=(1 << 2) | (1 << 5),
        priorities={2: 3, 5: 7},
        enables={2: True, 5: True},
        threshold=0,
    )
    assert winner == 5 and meip == 1


def test_plic_full_fanout_picks_top_priority(plic_so) -> None:
    """All 8 sources pending+enabled at varying priorities → source with
    max priority wins; if tied, lowest ID."""
    dut = fresh_dut(plic_so)
    winner, meip = _drive(dut,
        sources=0b1_1111_1110,  # sources 1..8
        # ascending priority; source 8 has the top
        priorities={i: i for i in range(1, 9)},
        enables={i: True for i in range(1, 9)},
        threshold=0,
    )
    assert winner == 8 and meip == 1


# ── claim / complete ──────────────────────────────────────────────────────
#
# These tests step the clock so `c0_claimed_r` can update between cycles.
# To avoid re-plumbing the full register block, we drive `PlicLogic`'s
# pulse + `hwif_out.claim_0_value` inputs directly — exactly what the
# emitted register block would produce on an AXI read / write.
#
# Claim timing (real HW, faithfully mirrored here):
#   T : read_pulse=1, hwif_out.claim_0_value = <winner returned to SW>
#       tick() → c0_claimed_r |= 1 << winner
#   T+1: read_pulse=0, arbitration now masks the winner
#
# Complete timing:
#   T : write_pulse=1    (register block sees SW write)
#       tick() → c0_wr_pulse_d = 1
#   T+1: write_pulse=0, hwif_out.claim_0_value = <SW-written complete id>
#       tick() → c0_claimed_r &= ~(1 << complete_id)


def _apply_inputs(dut, *, sources: int, priorities: dict[int, int],
                  enables: dict[int, bool], threshold: int) -> None:
    """Drive the raw PlicLogic inputs (no eval / tick)."""
    dut.source_in = sources
    for i in range(9):
        setattr(dut.hwif_out, f"priority_{i}_value", priorities.get(i, 0))
    dut.hwif_out.enable_0_value = sum(
        (1 << i) if enables.get(i, False) else 0 for i in range(9)
    )
    dut.hwif_out.threshold_0_value = threshold


def _winner(dut) -> int:
    dut.eval_comb()
    return int(dut.hwif_in.claim_0_value)


def _claim_read(dut, winner_id: int) -> None:
    """One-cycle SW read of claim_0: pulse read_pulse high and latch
    `claim_0_value = winner_id` to mirror the register block's storage."""
    dut.hwif_out.claim_0_value = winner_id
    dut.claim_0_read_pulse = 1
    dut.claim_0_write_pulse = 0
    tick(dut)
    dut.claim_0_read_pulse = 0


def _complete_write(dut, complete_id: int) -> None:
    """Two-cycle SW complete: T fires write_pulse, T+1 presents the
    written ID on hwif_out (matches the 1-cycle storage-settle delay)."""
    dut.claim_0_write_pulse = 1
    tick(dut)
    dut.claim_0_write_pulse = 0
    dut.hwif_out.claim_0_value = complete_id
    tick(dut)


def test_plic_claim_masks_source_from_next_arbitration(plic_so) -> None:
    """A claim read marks the returned source as in-service; on the next
    cycle that source drops out of arbitration and the winner becomes 0
    (since there are no other candidates)."""
    dut = fresh_dut(plic_so)
    reset(dut)
    _apply_inputs(dut,
        sources=(1 << 4),
        priorities={4: 5},
        enables={4: True},
        threshold=0,
    )
    # Pre-claim: source 4 is the winner.
    assert _winner(dut) == 4
    # SW reads claim_0 → latches in-service for source 4.
    _claim_read(dut, winner_id=4)
    # Next cycle: candidate[4] masked, no other sources → no winner.
    assert _winner(dut) == 0
    assert int(dut.intr_out) & 1 == 0


def test_plic_claim_unblocks_next_priority(plic_so) -> None:
    """Claim source 5 (prio 7); source 3 (prio 3) should become the new
    winner because the arbiter now excludes source 5."""
    dut = fresh_dut(plic_so)
    reset(dut)
    _apply_inputs(dut,
        sources=(1 << 3) | (1 << 5),
        priorities={3: 3, 5: 7},
        enables={3: True, 5: True},
        threshold=0,
    )
    assert _winner(dut) == 5
    _claim_read(dut, winner_id=5)
    # Source 5 in-service → source 3 now wins.
    assert _winner(dut) == 3
    assert int(dut.intr_out) & 1 == 1


def test_plic_complete_releases_source(plic_so) -> None:
    """After the full claim → complete round-trip, source is eligible
    again (level-triggered source stays high)."""
    dut = fresh_dut(plic_so)
    reset(dut)
    _apply_inputs(dut,
        sources=(1 << 2),
        priorities={2: 4},
        enables={2: True},
        threshold=0,
    )
    assert _winner(dut) == 2
    _claim_read(dut, winner_id=2)
    assert _winner(dut) == 0, "mid-service: source 2 must be masked"

    _complete_write(dut, complete_id=2)
    # Source 2 released → arbitration sees it again.
    assert _winner(dut) == 2
    assert int(dut.intr_out) & 1 == 1


def test_plic_claim_of_no_winner_is_noop(plic_so) -> None:
    """Reading claim when the winner is 0 (no pending sources passing
    threshold) must NOT latch anything into the in-service state."""
    dut = fresh_dut(plic_so)
    reset(dut)
    _apply_inputs(dut,
        sources=0, priorities={}, enables={}, threshold=0,
    )
    assert _winner(dut) == 0
    _claim_read(dut, winner_id=0)
    # Now pull a source high. It must be eligible — no stale in-service
    # bit hanging around.
    _apply_inputs(dut,
        sources=(1 << 6),
        priorities={6: 2},
        enables={6: True},
        threshold=0,
    )
    assert _winner(dut) == 6


def test_plic_complete_of_unclaimed_source_is_harmless(plic_so) -> None:
    """SW writing a complete for a source that was never claimed doesn't
    corrupt the in-service state — the matching clear-mask bit was
    already 0, so the AND-with-NOT-mask is a no-op."""
    dut = fresh_dut(plic_so)
    reset(dut)
    _apply_inputs(dut,
        sources=(1 << 1),
        priorities={1: 2},
        enables={1: True},
        threshold=0,
    )
    # Claim source 1.
    assert _winner(dut) == 1
    _claim_read(dut, winner_id=1)
    assert _winner(dut) == 0
    # SW mistakenly completes source 7 (not claimed). Must not release source 1.
    _complete_write(dut, complete_id=7)
    assert _winner(dut) == 0, "source 1 must still be in-service"
    # Issue the real complete; source 1 returns.
    _complete_write(dut, complete_id=1)
    assert _winner(dut) == 1
