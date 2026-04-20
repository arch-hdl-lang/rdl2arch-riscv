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
    return sorted(RDL_DIR.glob("*.rdl"))


def run_arch(arch_bin: str, cmd: str, files: list[Path], cwd: Path
             ) -> subprocess.CompletedProcess:
    return subprocess.run(
        [arch_bin, cmd, *[str(f) for f in files]],
        cwd=cwd, capture_output=True, text=True,
    )
