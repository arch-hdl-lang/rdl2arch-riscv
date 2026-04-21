"""Phase 6.1 — Ibex + CLINT + PLIC SoC elaboration check.

Runs Verilator in `--lint-only` mode against the full SoC filelist
(`ibex_mini_soc` + real Ibex core + our generated CLINT/PLIC). Purpose:
prove the HDL we emit composes with a real RISC-V core — port widths,
hwif struct field names, bus directions, etc. — without needing a test
program to actually execute (that's Phase 6.2).

The fusesoc-generated .vc files use paths relative to the build dir,
so we `cd` there before invoking Verilator.
"""

from __future__ import annotations

import subprocess
from pathlib import Path

import pytest


def test_ibex_mini_soc_lints(
    verilator_bin: str,
    ibex_soc_filelist: dict,
) -> None:
    vc_path: Path = ibex_soc_filelist["vc_path"]
    extra_sv: list[Path] = ibex_soc_filelist["extra_sv"]
    build_dir: Path = ibex_soc_filelist["build_dir"]

    cmd = [
        verilator_bin,
        "--lint-only",
        "-Wall",
        "-Wno-UNUSEDSIGNAL",   # Ibex ties a lot of observability ports off
        "-Wno-UNUSEDPARAM",
        "-Wno-PINMISSING",     # we don't connect every tracing port
        "-Wno-WIDTHEXPAND",    # $readmemh vs. sized vectors — benign
        "-Wno-IMPORTSTAR",     # arch-com emits `import Pkg::*;` at $unit
        "--unroll-count", "72",  # required by prim_secded per Verilator#1266
        "-f", str(vc_path),
        "--top-module", "ibex_mini_soc",
    ]
    cmd.extend(str(p) for p in extra_sv)

    result = subprocess.run(
        cmd, cwd=str(build_dir),
        capture_output=True, text=True,
    )
    # Useful context on failure.
    if result.returncode != 0:
        pytest.fail(
            "verilator --lint-only failed:\n"
            f"CMD:\n  {' '.join(cmd)}\n"
            f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )
