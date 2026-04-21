"""End-to-end: RDL fixture → rdl2arch-riscv → arch build must succeed."""

from pathlib import Path

import pytest
from systemrdl import RDLCompiler

from rdl2arch_riscv import RiscvClintExporter, RiscvCsrExporter
from rdl2arch_riscv.udps import ALL_UDPS

from conftest import clint_fixtures, rdl_fixtures, run_arch


def _compile(rdl_file: Path):
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(rdl_file))
    return rdlc.elaborate()


def _arch_check_and_build(arch_bin: str, out_dir: Path) -> None:
    arch_inputs = sorted(out_dir.glob("*.arch"))
    check = run_arch(arch_bin, "check", arch_inputs, out_dir)
    assert check.returncode == 0, (
        f"arch check failed:\nSTDERR:\n{check.stderr}\nSTDOUT:\n{check.stdout}"
    )
    build = run_arch(arch_bin, "build", arch_inputs, out_dir)
    assert build.returncode == 0, (
        f"arch build failed:\nSTDERR:\n{build.stderr}\nSTDOUT:\n{build.stdout}"
    )
    svs = sorted(out_dir.glob("*.sv"))
    assert svs, "arch build did not emit any .sv output"


@pytest.mark.parametrize("rdl_file", rdl_fixtures(), ids=lambda p: p.stem)
def test_csr_arch_build(rdl_file: Path, arch_bin: str, tmp_path: Path) -> None:
    root = _compile(rdl_file)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    files = RiscvCsrExporter().export(root.top, str(out_dir))
    assert files, "exporter produced no files"
    _arch_check_and_build(arch_bin, out_dir)


@pytest.mark.parametrize("rdl_file", clint_fixtures(), ids=lambda p: p.stem)
def test_clint_arch_build(rdl_file: Path, arch_bin: str, tmp_path: Path) -> None:
    root = _compile(rdl_file)
    out_dir = tmp_path / "out"
    out_dir.mkdir()
    files = RiscvClintExporter().export(root.top, str(out_dir))
    assert files, "clint exporter produced no files"
    _arch_check_and_build(arch_bin, out_dir)
