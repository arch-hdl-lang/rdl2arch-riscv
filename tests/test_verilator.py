"""End-to-end verification against the emitted SystemVerilog via
Verilator + cocotb.

Flow per fixture:
  1. Emit rdl2arch-riscv output + generated integrated-top ARCH.
  2. `arch build *.arch` → produces `*.sv` files.
  3. Verilate + run a cocotb test module against the integrated top.

Only the `integrated` target is verilated for now — per-module SV
parity is lower value since each module's semantics are already
covered by the pybind layer.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path

import pytest
from systemrdl import RDLCompiler

from rdl2arch_riscv import RiscvCsrExporter
from rdl2arch_riscv.scan_csrs import scan
from rdl2arch_riscv.udps import ALL_UDPS

from conftest import RDL_DIR
from sim.integrated_top import emit_integrated_top, integrated_top_name


COCOTB_TESTS_DIR = Path(__file__).parent / "cocotb_sv" / "cocotb_tests"


pytest.importorskip("cocotb_tools.runner")
if shutil.which("verilator") is None:
    pytest.skip("Verilator not found on PATH", allow_module_level=True)


def _arch_build(arch_bin: str, out_dir: Path) -> list[Path]:
    arch_inputs = sorted(out_dir.glob("*.arch"))
    result = subprocess.run(
        [arch_bin, "build", *[str(p) for p in arch_inputs]],
        cwd=out_dir, capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"arch build failed:\nSTDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )
    return sorted(out_dir.glob("*.sv"))


def _emit_all(rdl_file: Path, out_dir: Path) -> str:
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(rdl_file))
    root = rdlc.elaborate()
    RiscvCsrExporter().export(root.top, str(out_dir))
    design = scan(root.top, xlen=32)
    top_name = integrated_top_name(design)
    (out_dir / f"{top_name}.arch").write_text(emit_integrated_top(design))
    return top_name


@pytest.mark.parametrize("rdl_stem", ["mtrap_subset"])
def test_verilator_integrated(rdl_stem: str, arch_bin: str,
                              tmp_path: Path) -> None:
    from cocotb_tools.runner import get_runner

    rdl_file = RDL_DIR / f"{rdl_stem}.rdl"
    out_dir = tmp_path / "gen"
    out_dir.mkdir()
    top_name = _emit_all(rdl_file, out_dir)

    sv_files = _arch_build(arch_bin, out_dir)
    assert sv_files, "arch build produced no .sv output"
    # Package .sv must come first for Verilator's import-order rules.
    sv_files.sort(key=lambda p: (0 if "Pkg" in p.name else 1, p.name))

    runner = get_runner("verilator")
    build_dir = tmp_path / "sim_build"
    runner.build(
        sources=[str(p) for p in sv_files],
        hdl_toplevel=top_name,
        build_dir=str(build_dir),
        always=True,
        build_args=["-Wno-IMPORTSTAR", "-Wno-WIDTHEXPAND", "-Wno-UNUSEDSIGNAL"],
    )

    results_xml = runner.test(
        test_module="test_integrated",
        hdl_toplevel=top_name,
        build_dir=str(build_dir),
        test_dir=str(COCOTB_TESTS_DIR),
        results_xml=str(tmp_path / f"results_{rdl_stem}.xml"),
    )
    import xml.etree.ElementTree as ET
    tree = ET.parse(results_xml)
    root = tree.getroot()
    failures = (int(root.attrib.get("failures", "0"))
                + int(root.attrib.get("errors", "0")))
    assert failures == 0, f"cocotb reported {failures} failures; see {results_xml}"
