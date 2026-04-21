"""Functional sim tests for the emitted CLINT logic module.

Builds `ClintLogic` via `arch sim --pybind` and exercises the three
behaviors the module is responsible for:

  * msip passthrough   (msip_out == msip_value[0])
  * mtime >= mtimecmp  (mtip rises at the right time, with 64-bit arithmetic)
  * mtime increment    (hwif_in.mtime_{lo,hi}_v advances when mtime_tick high)
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import RDL_DIR
from sim.driver import reset, tick
from sim.harness import fresh_dut


pytest.importorskip("pybind11")


@pytest.fixture(scope="session")
def clint_so(arch_bin, tmp_path_factory) -> str:
    """Build CLINT's register block + logic module once, pick the Logic .so."""
    from rdl2arch_riscv import RiscvClintExporter
    from rdl2arch_riscv.udps import ALL_UDPS
    from systemrdl import RDLCompiler

    out = tmp_path_factory.mktemp("clint_sim")
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(RDL_DIR / "clint_basic.rdl"))
    RiscvClintExporter().export(rdlc.elaborate().top, str(out))

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
    so_files = list(build_dir.glob("VClintLogic_pybind.*.so"))
    assert so_files, f"ClintLogic .so not emitted in {build_dir}"
    return str(so_files[0])


def _prime(dut, *, msip: int = 0, mtime_lo: int = 0, mtime_hi: int = 0,
           mtimecmp_lo: int = 0xFFFFFFFF, mtimecmp_hi: int = 0xFFFFFFFF,
           tick_en: int = 0) -> None:
    dut.hwif_out.msip_value = msip
    dut.hwif_out.msip_reserved = 0
    dut.hwif_out.mtime_lo_v = mtime_lo
    dut.hwif_out.mtime_hi_v = mtime_hi
    dut.hwif_out.mtimecmp_lo_v = mtimecmp_lo
    dut.hwif_out.mtimecmp_hi_v = mtimecmp_hi
    dut.mtime_tick = tick_en


def test_msip_passthrough_bit0(clint_so) -> None:
    """msip_out tracks bit 0 of msip_value. Higher bits don't matter
    because the RDL only declares value[0:0] as the pending bit."""
    dut = fresh_dut(clint_so)
    _prime(dut, msip=0)
    dut.eval_comb()
    assert dut.msip_out == 0
    _prime(dut, msip=1)
    dut.eval_comb()
    assert dut.msip_out == 1


def test_mtip_fires_when_mtime_meets_mtimecmp(clint_so) -> None:
    """mtip_out = (mtime >= mtimecmp), 64-bit compare."""
    dut = fresh_dut(clint_so)
    _prime(dut, mtime_lo=0xFFFF_FFFE, mtime_hi=0, mtimecmp_lo=0xFFFF_FFFF, mtimecmp_hi=0)
    dut.eval_comb()
    assert dut.mtip_out == 0, "mtime 0xFFFF_FFFE < mtimecmp 0xFFFF_FFFF"
    _prime(dut, mtime_lo=0xFFFF_FFFF, mtime_hi=0, mtimecmp_lo=0xFFFF_FFFF, mtimecmp_hi=0)
    dut.eval_comb()
    assert dut.mtip_out == 1, "mtime == mtimecmp should fire"


def test_mtip_uses_full_64bit_compare(clint_so) -> None:
    """mtip must not fire prematurely when the low 32 bits wrap but the
    high 32 bits are still below. Guards against a sloppy 32-bit compare."""
    dut = fresh_dut(clint_so)
    _prime(dut,
           mtime_lo=0xFFFFFFFF, mtime_hi=0,
           mtimecmp_lo=0x00000000, mtimecmp_hi=0x00000001)
    dut.eval_comb()
    # mtime = 0x0000_0000_FFFF_FFFF, mtimecmp = 0x0000_0001_0000_0000
    # mtime < mtimecmp, so mtip low.
    assert dut.mtip_out == 0
    _prime(dut,
           mtime_lo=0x00000000, mtime_hi=0x00000001,
           mtimecmp_lo=0x00000000, mtimecmp_hi=0x00000001)
    dut.eval_comb()
    assert dut.mtip_out == 1


def test_mtime_increments_when_tick_high(clint_so) -> None:
    """hwif_in.mtime_lo_v / mtime_hi_v carry the next-cycle mtime value.
    When tick is high, it's mtime+1; low, it's mtime unchanged."""
    dut = fresh_dut(clint_so)
    _prime(dut, mtime_lo=100, mtime_hi=0, tick_en=1)
    dut.eval_comb()
    assert dut.hwif_in.mtime_lo_v == 101
    assert dut.hwif_in.mtime_hi_v == 0

    # Low-to-high carry
    _prime(dut, mtime_lo=0xFFFFFFFF, mtime_hi=0, tick_en=1)
    dut.eval_comb()
    assert dut.hwif_in.mtime_lo_v == 0
    assert dut.hwif_in.mtime_hi_v == 1

    # tick low: pass through unchanged
    _prime(dut, mtime_lo=0xCAFE_F00D, mtime_hi=0x1234_5678, tick_en=0)
    dut.eval_comb()
    assert dut.hwif_in.mtime_lo_v == 0xCAFE_F00D
    assert dut.hwif_in.mtime_hi_v == 0x1234_5678
