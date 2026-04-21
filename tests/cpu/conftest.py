"""Shared fixtures for the Ibex SoC integration tests.

These tests bolt our generated CLINT + PLIC onto a real RISC-V core
(lowRISC Ibex) and exercise the interrupt path end-to-end. Two external
dependencies are required:

  * `riscv64-elf-gcc` (homebrew formula) — RISC-V cross-compiler, used
    by the Phase-6.2 ISR tests. Not needed for the Phase-6.1 lint test.
  * An Ibex checkout at `$IBEX_ROOT` (default: `~/github/ibex`). Tests
    that need it `pytest.skip` when it's missing.

The `ibex_soc_filelist` fixture assembles, at session scope, a
`verilator`-ready command-file that combines:

  1. The Ibex file tree (pulled in via `fusesoc --setup`, which resolves
     the `lowrisc:ibex:ibex_top_tracing` + `lowrisc:ibex:sim_shared` dep
     trees and writes a `.vc` into a build directory). We strip the
     `--top-module`/`--exe` lines since our top is `ibex_mini_soc`.
  2. Our hand-written SoC glue (`ibex_mini_soc.sv`, `obi_to_axi_lite.sv`).
  3. Our generated CLINT + PLIC `.sv` files (`arch build` on the RDL
     fixtures in `tests/rdl/`).

Everything lands under a single temp build dir that's session-lived.
"""

from __future__ import annotations

import os
import shutil
import subprocess
from pathlib import Path
from typing import Optional

import pytest

from conftest import RDL_DIR


TESTS_DIR = Path(__file__).parent
SOC_DIR = TESTS_DIR / "soc"


def _find_ibex_root() -> Optional[Path]:
    env = os.environ.get("IBEX_ROOT")
    if env:
        p = Path(env).expanduser()
        return p if p.is_dir() else None
    default = Path.home() / "github" / "ibex"
    return default if default.is_dir() else None


@pytest.fixture(scope="session")
def ibex_root() -> Path:
    p = _find_ibex_root()
    if p is None:
        pytest.skip(
            "Ibex checkout not found. Clone lowRISC/ibex to ~/github/ibex "
            "or set IBEX_ROOT=/path/to/ibex."
        )
    return p


def _require(tool: str) -> None:
    if shutil.which(tool) is None:
        pytest.skip(f"required tool `{tool}` not on PATH")


@pytest.fixture(scope="session")
def fusesoc_bin() -> str:
    _require("fusesoc")
    return "fusesoc"


@pytest.fixture(scope="session")
def verilator_bin() -> str:
    _require("verilator")
    return "verilator"


def _generate_clint_plic_sv(arch_bin: str, out_dir: Path) -> list[Path]:
    """Run the existing arch-com pipeline to produce CLINT + PLIC `.sv`.

    Uses the same RDL fixtures (`clint_basic`, `plic_basic`) that the
    unit / sim tests consume, so the SoC-level test exercises the exact
    same emitted HDL. Returns the list of generated .sv files.
    """
    from systemrdl import RDLCompiler
    from rdl2arch_riscv import RiscvClintExporter, RiscvPlicExporter
    from rdl2arch_riscv.udps import ALL_UDPS

    out_dir.mkdir(parents=True, exist_ok=True)
    generated: list[Path] = []

    # plic_multictx (2 M-mode contexts) is a strict superset of
    # plic_basic — single-context tests only ever touch ctx 0, whose
    # behaviour is identical. Using multictx everywhere lets the
    # Phase-6.4 multictx_isr test share the SoC build with the
    # Phase-6.2 / 6.3 ones.
    for rdl_name, exporter_cls in (
        ("clint_basic", RiscvClintExporter),
        ("plic_multictx", RiscvPlicExporter),
    ):
        stage = out_dir / rdl_name
        stage.mkdir(exist_ok=True)
        rdlc = RDLCompiler()
        for udp in ALL_UDPS:
            rdlc.register_udp(udp, soft=False)
        rdlc.compile_file(str(RDL_DIR / f"{rdl_name}.rdl"))
        exporter_cls().export(rdlc.elaborate().top, str(stage))

        archs = sorted(stage.glob("*.arch"))
        # `arch build` writes the generated .sv next to the .arch inputs;
        # no `-o` knob needed.
        result = subprocess.run(
            [arch_bin, "build", *[str(p) for p in archs]],
            capture_output=True, text=True,
            cwd=str(stage),
        )
        if result.returncode != 0:
            raise RuntimeError(
                f"arch build failed for {rdl_name}:\n{result.stderr}"
            )
        # Verilator needs the package files before any module that
        # imports them. `Pkg.sv` goes first, then everything else in
        # alphabetical order.
        all_sv = sorted(stage.glob("*.sv"))
        pkgs   = [p for p in all_sv if p.name.endswith("Pkg.sv")]
        nonpkg = [p for p in all_sv if not p.name.endswith("Pkg.sv")]
        generated.extend(pkgs + nonpkg)

    return generated


def _fusesoc_setup(ibex_root: Path, build_root: Path, fusesoc_bin: str) -> Path:
    """Run `fusesoc --setup` to resolve Ibex's dep tree and write a
    Verilator `.vc` file. Returns the path to the .vc."""
    build_root.mkdir(parents=True, exist_ok=True)

    # We resolve two roots — ibex_top_tracing (the core + tracer) and
    # sim_shared (ram_2p, simulator_ctrl, etc). sim_shared isn't a
    # top-level target so we stage it as a dependency of a tiny wrapper
    # core that we don't actually run; instead, we let fusesoc emit the
    # ibex_top_tracing lint tree and manually append the shared-SV files
    # we need.
    result = subprocess.run(
        [fusesoc_bin, f"--cores-root={ibex_root}",
         "run", "--target=lint", "--setup",
         "lowrisc:ibex:ibex_top_tracing"],
        cwd=str(build_root),
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"fusesoc setup failed:\n{result.stderr}\n{result.stdout}"
        )

    # The VC file path depends on the resolved version string. Glob for it.
    vc_candidates = list(
        (build_root / "build").glob("*/lint-verilator/*.vc")
    )
    if not vc_candidates:
        raise RuntimeError(
            f"no .vc produced under {build_root}/build/*/lint-verilator/"
        )
    return vc_candidates[0]


def _strip_top_and_exe(vc_path: Path) -> str:
    """Return the .vc contents with the fusesoc-tagged top-module /
    parameter / exe lines removed. We set our own top (`ibex_mini_soc`)
    and its parameter surface is a strict subset of `ibex_top_tracing`,
    so the fusesoc-supplied `-GRV32E=0` etc. would error out with
    "parameters from the command line were not found in the design".
    Keep the `-D` macro defines — those configure ibex_pkg itself and
    our top needs them too."""
    out = []
    for line in vc_path.read_text().splitlines():
        s = line.strip()
        if (
            s.startswith("--top-module")
            or s == "--exe"
            or s.startswith("-G")      # top-level parameters, now stale
        ):
            continue
        out.append(line)
    return "\n".join(out) + "\n"


@pytest.fixture(scope="session")
def ibex_soc_filelist(
    arch_bin: str,
    ibex_root: Path,
    fusesoc_bin: str,
    tmp_path_factory: pytest.TempPathFactory,
) -> dict[str, object]:
    """Build the full Verilator filelist for the SoC and return a dict:

        {
          "vc_path":   Path,      # fusesoc-generated .vc (stripped)
          "extra_sv":  list[Path], # our SV + generated CLINT/PLIC .sv
          "extra_v":   list[Path], # Ibex's shared/rtl files we pull in manually
          "build_dir": Path,      # where the .vc lives (Verilator cwd)
        }
    """
    build_root = tmp_path_factory.mktemp("ibex_soc_build")

    # 1. Generate CLINT + PLIC .sv.
    generated_dir = build_root / "generated"
    gen_sv = _generate_clint_plic_sv(arch_bin, generated_dir)

    # 2. Resolve Ibex deps via fusesoc.
    vc_path = _fusesoc_setup(ibex_root, build_root, fusesoc_bin)
    stripped = _strip_top_and_exe(vc_path)
    stripped_vc = vc_path.with_suffix(".stripped.vc")
    stripped_vc.write_text(stripped)

    # 3. Our hand-written SoC glue.
    soc_sv = [
        SOC_DIR / "obi_to_axi_lite.sv",
        SOC_DIR / "ibex_mini_soc.sv",
    ]

    # 4. Shared Ibex sim helpers that sim_shared ships (ram_2p, simulator_ctrl).
    #    We avoid its `bus.sv` / `timer.sv` — we roll our own bus in the top
    #    and our generated CLINT replaces the timer.
    shared_sv = [
        ibex_root / "shared" / "rtl" / "ram_2p.sv",
        ibex_root / "shared" / "rtl" / "sim" / "simulator_ctrl.sv",
    ]
    # Sanity check — catch a move by lowRISC early.
    for p in shared_sv:
        if not p.is_file():
            pytest.skip(f"Ibex shared SV missing at {p} — repo layout changed?")

    return {
        "vc_path":   stripped_vc,
        "extra_sv":  gen_sv + soc_sv + shared_sv,
        "build_dir": stripped_vc.parent,
    }
