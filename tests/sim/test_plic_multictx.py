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
from sim.driver import reset, tick
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


# ── claim / complete — per-context independence ───────────────────────────
#
# Key property: each context keeps its own in-service bitmap. A claim
# on context 0 MUST NOT mask sources from context 1's arbitration, and
# vice versa. Completes are similarly scoped. See `test_plic.py` for
# timing commentary on the read / write pulses.


def _apply_inputs(dut, *, sources: int, priorities: dict[int, int],
                  enables: tuple[int, int], thresholds: tuple[int, int]) -> None:
    dut.source_in = sources
    for i in range(9):
        setattr(dut.hwif_out, f"priority_{i}_value", priorities.get(i, 0))
    dut.hwif_out.enable_0_value = enables[0]
    dut.hwif_out.enable_1_value = enables[1]
    dut.hwif_out.threshold_0_value = thresholds[0]
    dut.hwif_out.threshold_1_value = thresholds[1]


def _winners(dut) -> dict:
    dut.eval_comb()
    return {
        "ctx0": int(dut.hwif_in.claim_0_value),
        "ctx1": int(dut.hwif_in.claim_1_value),
        "intr": int(dut.intr_out),
    }


def _pulse_init(dut) -> None:
    dut.claim_0_read_pulse = 0
    dut.claim_0_write_pulse = 0
    dut.claim_1_read_pulse = 0
    dut.claim_1_write_pulse = 0


def _claim_read(dut, ctx: int, winner_id: int) -> None:
    setattr(dut.hwif_out, f"claim_{ctx}_value", winner_id)
    setattr(dut, f"claim_{ctx}_read_pulse", 1)
    tick(dut)
    setattr(dut, f"claim_{ctx}_read_pulse", 0)


def _complete_write(dut, ctx: int, complete_id: int) -> None:
    setattr(dut, f"claim_{ctx}_write_pulse", 1)
    tick(dut)
    setattr(dut, f"claim_{ctx}_write_pulse", 0)
    setattr(dut.hwif_out, f"claim_{ctx}_value", complete_id)
    tick(dut)


def test_multictx_claim_does_not_cross_contexts(plic_multictx_so) -> None:
    """Source 4 pending+enabled on both contexts. Claim it on context 0.
    Context 0 masks source 4 next cycle; context 1's arbitration stays
    untouched — per-context `claimed_r` is the whole point."""
    dut = fresh_dut(plic_multictx_so)
    reset(dut)
    _pulse_init(dut)
    _apply_inputs(dut,
        sources=(1 << 4),
        priorities={4: 3},
        enables=((1 << 4), (1 << 4)),
        thresholds=(0, 0),
    )
    # Both contexts initially agree on the winner.
    r = _winners(dut)
    assert r["ctx0"] == 4
    assert r["ctx1"] == 4
    assert r["intr"] == 0b11

    _claim_read(dut, ctx=0, winner_id=4)

    # After ctx 0 claim: ctx 0 mask kicks in; ctx 1 still sees source 4.
    r = _winners(dut)
    assert r["ctx0"] == 0
    assert r["ctx1"] == 4
    assert r["intr"] == 0b10, f"intr={r['intr']:#b}"


def test_multictx_complete_scoped_to_one_context(plic_multictx_so) -> None:
    """Claim source 2 on BOTH contexts (both see it masked), then complete
    only on ctx 0. Source 2 re-appears for ctx 0 but stays masked on ctx 1."""
    dut = fresh_dut(plic_multictx_so)
    reset(dut)
    _pulse_init(dut)
    _apply_inputs(dut,
        sources=(1 << 2),
        priorities={2: 4},
        enables=((1 << 2), (1 << 2)),
        thresholds=(0, 0),
    )
    r = _winners(dut)
    assert r["ctx0"] == 2 and r["ctx1"] == 2

    _claim_read(dut, ctx=0, winner_id=2)
    _claim_read(dut, ctx=1, winner_id=2)

    r = _winners(dut)
    assert r["ctx0"] == 0 and r["ctx1"] == 0, "both contexts in-service"

    _complete_write(dut, ctx=0, complete_id=2)
    r = _winners(dut)
    assert r["ctx0"] == 2, "ctx 0 released source 2"
    assert r["ctx1"] == 0, "ctx 1 still in-service"
    assert r["intr"] == 0b01


def test_multictx_simultaneous_claims_each_context(plic_multictx_so) -> None:
    """Two sources, one enabled per context. Claim the per-context winners
    on the same cycle; next cycle both contexts report no winner."""
    dut = fresh_dut(plic_multictx_so)
    reset(dut)
    _pulse_init(dut)
    _apply_inputs(dut,
        sources=(1 << 2) | (1 << 5),
        priorities={2: 3, 5: 3},
        enables=((1 << 2), (1 << 5)),
        thresholds=(0, 0),
    )
    r = _winners(dut)
    assert r["ctx0"] == 2 and r["ctx1"] == 5

    # Simultaneous claim read on both contexts — single tick advances both.
    dut.hwif_out.claim_0_value = 2
    dut.hwif_out.claim_1_value = 5
    dut.claim_0_read_pulse = 1
    dut.claim_1_read_pulse = 1
    tick(dut)
    dut.claim_0_read_pulse = 0
    dut.claim_1_read_pulse = 0

    r = _winners(dut)
    assert r["ctx0"] == 0 and r["ctx1"] == 0
    assert r["intr"] == 0
