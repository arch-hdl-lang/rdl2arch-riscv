"""Functional sim tests for the emitted PLIC logic module.

Builds `PlicLogic` via `arch sim --pybind` and exercises the
arbiter's priority/enable/threshold/tiebreak behavior against the
8-source, 1-context fixture.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import RDL_DIR
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
