"""Golden-output diff tests.

For every RDL fixture, regenerate the rdl2arch-riscv output (package +
CSR file + access controller + trap coordinator) plus the test-only
integrated-top wrapper, and diff against checked-in expected output
under `tests/expected/<fixture>/`. Catches unintended emitter changes —
including ones that still pass `arch build` but alter the emitted code
in surprising ways.

To refresh the golden files after an intentional emitter change, run:
    UPDATE_GOLDEN=1 pytest tests/test_golden.py
"""

from __future__ import annotations

import os
from pathlib import Path

import pytest
from systemrdl import RDLCompiler

from rdl2arch_riscv import RiscvClintExporter, RiscvCsrExporter
from rdl2arch_riscv.scan_csrs import scan
from rdl2arch_riscv.udps import ALL_UDPS

from conftest import clint_fixtures, rdl_fixtures
from sim.integrated_top import emit_integrated_top, integrated_top_name


EXPECTED_DIR = Path(__file__).parent / "expected"


def _update_mode() -> bool:
    return os.environ.get("UPDATE_GOLDEN") == "1"


def _compile(rdl_file: Path):
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(rdl_file))
    return rdlc.elaborate()


def _generate_csr(rdl_file: Path, out_dir: Path) -> dict[str, str]:
    """CSR fixtures emit: pkg + CsrFile + CsrAccess + CsrTrapCoord +
    integrated-top wrapper."""
    root = _compile(rdl_file)
    RiscvCsrExporter().export(root.top, str(out_dir))
    design = scan(root.top, xlen=32)
    top_name = integrated_top_name(design)
    (out_dir / f"{top_name}.arch").write_text(emit_integrated_top(design))
    return {p.name: p.read_text() for p in sorted(out_dir.glob("*.arch"))}


def _generate_clint(rdl_file: Path, out_dir: Path) -> dict[str, str]:
    """CLINT fixtures emit: pkg + register block (from rdl2arch) +
    Logic module (from rdl2arch-riscv)."""
    root = _compile(rdl_file)
    RiscvClintExporter().export(root.top, str(out_dir))
    return {p.name: p.read_text() for p in sorted(out_dir.glob("*.arch"))}


def _run_golden(rdl_file: Path, tmp_path: Path, generate_fn) -> None:
    generated = generate_fn(rdl_file, tmp_path)
    expected_dir = EXPECTED_DIR / rdl_file.stem

    if _update_mode():
        expected_dir.mkdir(parents=True, exist_ok=True)
        # Purge stale files so removed outputs don't linger in the golden set.
        for stale in expected_dir.glob("*.arch"):
            if stale.name not in generated:
                stale.unlink()
        for name, content in generated.items():
            (expected_dir / name).write_text(content)
        return

    assert expected_dir.is_dir(), (
        f"Missing golden directory {expected_dir}. "
        f"Run: UPDATE_GOLDEN=1 pytest tests/test_golden.py"
    )
    expected = {p.name: p.read_text() for p in sorted(expected_dir.glob("*.arch"))}
    missing = set(expected) - set(generated)
    extra = set(generated) - set(expected)
    assert not missing, f"Generator no longer produces: {sorted(missing)}"
    assert not extra, f"Generator produced unexpected files: {sorted(extra)}"
    for name, gen_text in generated.items():
        assert gen_text == expected[name], (
            f"Mismatch in {rdl_file.stem}/{name}. "
            f"Run UPDATE_GOLDEN=1 pytest tests/test_golden.py to refresh if "
            f"the change was intentional."
        )


@pytest.mark.parametrize("rdl_file", rdl_fixtures(), ids=lambda p: p.stem)
def test_csr_golden(rdl_file: Path, tmp_path: Path) -> None:
    _run_golden(rdl_file, tmp_path, _generate_csr)


@pytest.mark.parametrize("rdl_file", clint_fixtures(), ids=lambda p: p.stem)
def test_clint_golden(rdl_file: Path, tmp_path: Path) -> None:
    _run_golden(rdl_file, tmp_path, _generate_clint)
