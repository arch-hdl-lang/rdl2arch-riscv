"""Multi-context PLIC arbitration sim tests.

Exercises per-context independence: the arbiter runs once per context
with its own enable / threshold, but shares sources and priorities.
Output is packed into a UInt<N_contexts> `intr_out` — bit 0 → context
0's meip, bit 1 → context 1's seip.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import RDL_DIR
from sim.harness import fresh_dut


pytest.importorskip("pybind11")


@pytest.fixture(scope="session")
def plic_multictx_so(arch_bin, tmp_path_factory) -> str:
    from rdl2arch_riscv import RiscvPlicExporter
    from rdl2arch_riscv.udps import ALL_UDPS
    from systemrdl import RDLCompiler

    out = tmp_path_factory.mktemp("plic_multictx_sim")
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(RDL_DIR / "plic_multictx.rdl"))
    RiscvPlicExporter().export(rdlc.elaborate().top, str(out))
    build = out / "sim"
    r = subprocess.run(
        [arch_bin, "sim", "--pybind", "-o", str(build),
         *[str(p) for p in sorted(out.glob("*.arch"))]],
        capture_output=True, text=True,
    )
    if r.returncode != 0:
        raise RuntimeError(f"arch sim --pybind failed:\n{r.stderr}")
    so = list(build.glob("VPlicMultictxLogic_pybind.*.so"))
    assert so, f"PlicMultictxLogic .so missing in {build}"
    return str(so[0])


def _drive(dut, *, sources: int, priorities: dict[int, int],
           enables: tuple[int, int], thresholds: tuple[int, int]) -> dict:
    """Return a dict of {winner_ctx0, winner_ctx1, intr_bitmap}."""
    dut.source_in = sources
    for i in range(9):
        setattr(dut.hwif_out, f"priority_{i}_value", priorities.get(i, 0))
    dut.hwif_out.enable_0_value = enables[0]
    dut.hwif_out.enable_1_value = enables[1]
    dut.hwif_out.threshold_0_value = thresholds[0]
    dut.hwif_out.threshold_1_value = thresholds[1]
    dut.eval_comb()
    return {
        "ctx0": int(dut.hwif_in.claim_0_value),
        "ctx1": int(dut.hwif_in.claim_1_value),
        "intr": int(dut.intr_out),
    }


def test_multictx_independent_enables(plic_multictx_so) -> None:
    """Source 3 pending; enabled only in context 0 → only ctx 0 fires."""
    dut = fresh_dut(plic_multictx_so)
    r = _drive(dut,
        sources=(1 << 3),
        priorities={3: 5},
        enables=((1 << 3), 0),   # ctx 0 enables, ctx 1 doesn't
        thresholds=(0, 0),
    )
    assert r["ctx0"] == 3
    assert r["ctx1"] == 0
    assert r["intr"] == 0b01   # bit 0 = ctx 0 fired


def test_multictx_independent_thresholds(plic_multictx_so) -> None:
    """Source 3 @ priority 5; ctx 0 threshold 0 (passes), ctx 1 threshold 5
    (blocks equal priority) → only ctx 0 fires."""
    dut = fresh_dut(plic_multictx_so)
    r = _drive(dut,
        sources=(1 << 3),
        priorities={3: 5},
        enables=((1 << 3), (1 << 3)),  # both ctx enable source 3
        thresholds=(0, 5),              # ctx 1 threshold equals priority → blocked
    )
    assert r["ctx0"] == 3
    assert r["ctx1"] == 0
    assert r["intr"] == 0b01


def test_multictx_different_winners_per_context(plic_multictx_so) -> None:
    """Sources 2 and 5 both pending. Context 0 enables only source 2;
    context 1 enables only source 5 — each context picks a different winner."""
    dut = fresh_dut(plic_multictx_so)
    r = _drive(dut,
        sources=(1 << 2) | (1 << 5),
        priorities={2: 3, 5: 3},
        enables=((1 << 2), (1 << 5)),
        thresholds=(0, 0),
    )
    assert r["ctx0"] == 2
    assert r["ctx1"] == 5
    assert r["intr"] == 0b11    # both contexts fire


def test_multictx_both_contexts_pick_same_winner(plic_multictx_so) -> None:
    """Identical enables + thresholds → both contexts agree on the winner."""
    dut = fresh_dut(plic_multictx_so)
    r = _drive(dut,
        sources=(1 << 7),
        priorities={7: 6},
        enables=((1 << 7), (1 << 7)),
        thresholds=(0, 0),
    )
    assert r["ctx0"] == 7
    assert r["ctx1"] == 7
    assert r["intr"] == 0b11


def test_multictx_no_source_no_fire(plic_multictx_so) -> None:
    dut = fresh_dut(plic_multictx_so)
    r = _drive(dut,
        sources=0,
        priorities={},
        enables=(0xFFFF, 0xFFFF),
        thresholds=(0, 0),
    )
    assert r["ctx0"] == 0
    assert r["ctx1"] == 0
    assert r["intr"] == 0
