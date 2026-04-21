"""Shared pytest fixtures for rdl2arch-riscv."""

import os
import shutil
import subprocess
from pathlib import Path

import pytest


TESTS_DIR = Path(__file__).parent
RDL_DIR = TESTS_DIR / "rdl"


def find_arch_binary() -> str | None:
    env = os.environ.get("ARCH_BIN")
    if env and Path(env).is_file():
        return env
    sibling = Path.home() / "github" / "arch-com" / "target" / "release" / "arch"
    if sibling.is_file():
        return str(sibling)
    which = shutil.which("arch")
    if which and "arch-com" not in which and which != "/usr/bin/arch":
        return which
    return None


@pytest.fixture(scope="session")
def arch_bin() -> str:
    path = find_arch_binary()
    if path is None:
        pytest.skip("ARCH compiler not found (set ARCH_BIN=/path/to/arch)")
    return path


def rdl_fixtures() -> list[Path]:
    """CSR fixtures consumed by RiscvCsrExporter. CLINT fixtures live
    alongside and follow the `clint_*.rdl` naming convention — split out
    so they go through RiscvClintExporter instead."""
    return sorted(p for p in RDL_DIR.glob("*.rdl")
                  if not p.name.startswith("clint_"))


def clint_fixtures() -> list[Path]:
    return sorted(RDL_DIR.glob("clint_*.rdl"))


@pytest.fixture(scope="session")
def mtrap_sim_build(arch_bin, tmp_path_factory) -> dict[str, str]:
    """Build all pybind targets from mtrap_subset.rdl once per session.

    Shared across test_csr_file.py, test_access_controller.py (mtrap arm),
    test_trap_coordinator.py, and test_integration.py — each picks its
    `.so` by target name from the returned dict. This relies on
    arch-com PR #40 emitting one `.so` per module from a single
    `arch sim --pybind` invocation.
    """
    pytest.importorskip("pybind11")
    from sim.harness import build_all_sim
    out = tmp_path_factory.mktemp("mtrap_sim")
    return build_all_sim(RDL_DIR / "mtrap_subset.rdl", out, arch_bin)


@pytest.fixture(scope="session")
def override_sim_build(arch_bin, tmp_path_factory) -> dict[str, str]:
    """Same idea for priv_override.rdl — only test_access_controller.py
    consumes this one currently."""
    pytest.importorskip("pybind11")
    from sim.harness import build_all_sim
    out = tmp_path_factory.mktemp("override_sim")
    return build_all_sim(RDL_DIR / "priv_override.rdl", out, arch_bin)


def run_arch(arch_bin: str, cmd: str, files: list[Path], cwd: Path
             ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [arch_bin, cmd, *[str(f) for f in files]],
        cwd=cwd, capture_output=True, text=True,
    )
