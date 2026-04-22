"""End-to-end interrupt integration test.

Instantiates the three M-mode interrupt controllers — CLINT, PLIC, and
the CSR file — each as its own pybind DUT, then cross-wires them in
Python to prove the output-to-input names line up for an actual
system-level hookup:

    ClintLogic.msip_out        →   CsrFile.hwif_in.mip_msip
    ClintLogic.mtip_out        →   CsrFile.hwif_in.mip_mtip
    PlicLogic.intr_out[0]      →   CsrFile.hwif_in.mip_meip

The PLIC's `intr_out` is a UInt<N_contexts> bitmap; bit 0 is context 0
(M-mode), so for the single-context `plic_basic` fixture we mask `& 1`
before handing it to the CSR file.

Each test drives an external stimulus (SW write to a CLINT/PLIC register
via its hwif_out, or an external `source_in` line to the PLIC) and then
reads back the `mip` CSR through the CSR file's pipeline interface to
confirm the expected bit went high.

An ARCH-level wrapper module would be cleaner but hits a bus-name
collision today: both the CLINT and PLIC register blocks emit their
own `bus AxiLite` at file scope with different address widths. Python-
level wiring side-steps that entirely and is sufficient to demonstrate
the integration.
"""

from __future__ import annotations

import subprocess

import pytest

from conftest import RDL_DIR
from sim.driver import CsrPipelineDriver, reset_async_low as reset, tick
from sim.harness import fresh_dut


pytest.importorskip("pybind11")


MIP = 0x344

# mip bit positions per RISC-V privileged spec
MIP_MSIP = 3
MIP_MTIP = 7
MIP_MEIP = 11


@pytest.fixture(scope="session")
def _build_aux_sim(arch_bin, tmp_path_factory):
    """Build ClintLogic + PlicLogic .so's once per session.

    The mtrap CSR file comes from the existing `mtrap_sim_build` fixture
    (in conftest.py). CLINT and PLIC each need their own RDL compile +
    arch-sim run since they feed different exporters.
    """
    from rdl2arch_riscv import RiscvClintExporter, RiscvPlicExporter
    from rdl2arch_riscv.udps import ALL_UDPS
    from systemrdl import RDLCompiler

    def _compile_export(rdl_path, exporter_cls, out):
        rdlc = RDLCompiler()
        for udp in ALL_UDPS:
            rdlc.register_udp(udp, soft=False)
        rdlc.compile_file(str(rdl_path))
        exporter_cls().export(rdlc.elaborate().top, str(out))

    def _run_pybind(out, target):
        build = out / "sim"
        arch_files = sorted(out.glob("*.arch"))
        r = subprocess.run(
            [arch_bin, "sim", "--pybind", "-o", str(build),
             *[str(p) for p in arch_files]],
            capture_output=True, text=True,
        )
        if r.returncode != 0:
            raise RuntimeError(
                f"arch sim --pybind failed for {target}:\n{r.stderr}"
            )
        matches = list(build.glob(f"V{target}_pybind.*.so"))
        assert matches, f"no {target} .so in {build}"
        return str(matches[0])

    clint_out = tmp_path_factory.mktemp("intr_clint")
    _compile_export(RDL_DIR / "clint_basic.rdl", RiscvClintExporter, clint_out)
    clint_logic = _run_pybind(clint_out, "ClintLogic")

    plic_out = tmp_path_factory.mktemp("intr_plic")
    _compile_export(RDL_DIR / "plic_basic.rdl", RiscvPlicExporter, plic_out)
    plic_logic = _run_pybind(plic_out, "PlicLogic")

    return {"clint_logic_so": clint_logic, "plic_logic_so": plic_logic}


def _make_system(mtrap_sim_build, aux):
    """Load the three DUTs and reset all of them."""
    csr    = fresh_dut(mtrap_sim_build["csr_file"])
    clint  = fresh_dut(aux["clint_logic_so"])
    plic   = fresh_dut(aux["plic_logic_so"])
    reset(csr)
    reset(clint)
    reset(plic)
    return csr, clint, plic


def _tick_with_wiring(csr, clint, plic) -> None:
    """Propagate controller outputs into the CSR file's hwif_in, then tick.

    Also settles every DUT's combinational logic so mid-tick reads see
    the up-to-date values. Order: eval_comb each → cross-wire the
    outputs → tick the CSR file (which latches hwif_in into mip state).
    """
    clint.eval_comb()
    plic.eval_comb()
    csr.hwif_in.mip_msip = clint.msip_out
    csr.hwif_in.mip_mtip = clint.mtip_out
    # plic.intr_out is a UInt<N_contexts> bitmap; plic_basic has one
    # context, so bit 0 is the M-mode external-interrupt line.
    csr.hwif_in.mip_meip = int(plic.intr_out) & 1
    tick(csr)


def test_clint_msip_reaches_csr_mip(mtrap_sim_build, _build_aux_sim) -> None:
    """SW-writable msip bit in CLINT flows through ClintLogic.msip_out
    into CsrFile.hwif_in.mip_msip and shows up in mip[3] on a CSR read."""
    csr, clint, plic = _make_system(mtrap_sim_build, _build_aux_sim)
    drv = CsrPipelineDriver(csr)

    # Initial: no source, no mip bits.
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MSIP) & 1 == 0

    # Simulate SW having written clint.msip register bit 0.
    clint.hwif_out.msip_value = 1
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MSIP) & 1 == 1, \
        f"MSIP should propagate from CLINT to mip[3]"

    # Clear it again.
    clint.hwif_out.msip_value = 0
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MSIP) & 1 == 0


def test_clint_timer_reaches_csr_mip(mtrap_sim_build, _build_aux_sim) -> None:
    """mtime >= mtimecmp fires mtip_out, which drives mip[7]."""
    csr, clint, plic = _make_system(mtrap_sim_build, _build_aux_sim)
    drv = CsrPipelineDriver(csr)

    # mtime 0 < mtimecmp 100 → no firing
    clint.hwif_out.mtime_lo_v = 0
    clint.hwif_out.mtime_hi_v = 0
    clint.hwif_out.mtimecmp_lo_v = 100
    clint.hwif_out.mtimecmp_hi_v = 0
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MTIP) & 1 == 0

    # mtime reaches mtimecmp → fires
    clint.hwif_out.mtime_lo_v = 100
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MTIP) & 1 == 1, \
        "MTIP should propagate from CLINT timer to mip[7]"


def test_plic_external_source_reaches_csr_mip(mtrap_sim_build, _build_aux_sim) -> None:
    """External source line → PLIC arbitration → intr_out[0] → mip[11]."""
    csr, clint, plic = _make_system(mtrap_sim_build, _build_aux_sim)
    drv = CsrPipelineDriver(csr)

    # Enable source 5 with priority 3, threshold 0. Keep source_in low.
    plic.hwif_out.enable_0_value = 1 << 5
    plic.hwif_out.priority_5_value = 3
    plic.hwif_out.threshold_0_value = 0
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MEIP) & 1 == 0

    # Pull source 5 high.
    plic.source_in = 1 << 5
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MEIP) & 1 == 1, \
        "MEIP should propagate from PLIC external source to mip[11]"

    # Drop the source.
    plic.source_in = 0
    _tick_with_wiring(csr, clint, plic)
    assert (drv.read(MIP) >> MIP_MEIP) & 1 == 0


def test_all_three_sources_simultaneous(mtrap_sim_build, _build_aux_sim) -> None:
    """Drive MSIP / MTIP / MEIP at once; all three bits should land in mip."""
    csr, clint, plic = _make_system(mtrap_sim_build, _build_aux_sim)
    drv = CsrPipelineDriver(csr)

    # CLINT: msip=1, mtime=42 > mtimecmp=10
    clint.hwif_out.msip_value = 1
    clint.hwif_out.mtime_lo_v = 42
    clint.hwif_out.mtime_hi_v = 0
    clint.hwif_out.mtimecmp_lo_v = 10
    clint.hwif_out.mtimecmp_hi_v = 0
    # PLIC: source 1 enabled, priority above threshold, source_in[1]=1
    plic.hwif_out.enable_0_value = 1 << 1
    plic.hwif_out.priority_1_value = 5
    plic.hwif_out.threshold_0_value = 0
    plic.source_in = 1 << 1

    _tick_with_wiring(csr, clint, plic)
    mip = drv.read(MIP)
    assert (mip >> MIP_MSIP) & 1 == 1, f"mip={mip:#x}"
    assert (mip >> MIP_MTIP) & 1 == 1, f"mip={mip:#x}"
    assert (mip >> MIP_MEIP) & 1 == 1, f"mip={mip:#x}"
