"""Phase-6.2 end-to-end timer ISR on Ibex + generated CLINT + generated PLIC.

Flow:

  1. Build the RV32 timer-ISR program via `make -C tests/cpu/sw`
     (produces `timer_isr.vmem`, ready for `$readmemh`). Skips when
     `riscv64-elf-gcc` isn't on PATH.

  2. Reuse the `ibex_soc_filelist` session fixture from conftest to
     pull in Ibex's dep tree + our generated CLINT/PLIC SV + our SoC
     glue. That fixture gives us the `.vc` command-file; we extract
     `+incdir+` lines and `.sv/.svh/.vlt` files out of it to feed
     cocotb's Verilator runner.

  3. Build the Verilator model with:
        -GSRAMInitFile="/path/to/timer_isr.vmem"  (elaboration-time preload)
        --public-flat-rw                          (let cocotb read mem[*])
        --unroll-count 72                         (prim_secded requirement)

  4. Run `cocotb_tools.runner.test(...)` against
     `cocotb_tests/test_timer_isr.py`. Surfaces any cocotb assertion as
     a pytest failure via the results.xml.
"""

from __future__ import annotations

import shutil
import subprocess
from pathlib import Path
from typing import List, Tuple

import pytest


TESTS_DIR = Path(__file__).parent
SW_DIR = TESTS_DIR / "sw"
COCOTB_TESTS_DIR = TESTS_DIR / "cocotb_tests"


pytest.importorskip("cocotb_tools.runner")


@pytest.fixture(scope="session")
def riscv_gcc() -> str:
    """`riscv64-elf-gcc` from `brew install riscv64-elf-gcc`.

    The Makefile's `CROSS` defaults to `riscv64-elf-`, so we just need
    the binary on PATH; no env-var plumbing. Skip the test (not fail)
    when the toolchain isn't installed."""
    tool = shutil.which("riscv64-elf-gcc")
    if tool is None:
        pytest.skip(
            "riscv64-elf-gcc not on PATH; install via "
            "`brew install riscv64-elf-gcc`"
        )
    return tool


def _build_sw(build_dir: Path) -> Path:
    """Invoke `make` to produce `timer_isr.vmem`. Returns the .vmem path."""
    build_dir.mkdir(parents=True, exist_ok=True)
    result = subprocess.run(
        ["make", "-C", str(SW_DIR), f"BUILD={build_dir}"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"make failed building the timer-ISR program:\n"
            f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )
    vmem = build_dir / "timer_isr.vmem"
    if not vmem.is_file():
        raise RuntimeError(f"expected {vmem} after `make`; not found")
    return vmem


def _parse_vc(vc_path: Path) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Split a Verilator command-file into (incdirs, sv_files, vlt_files, defines).

    cocotb-runner wants sources as a list rather than `-f`, and
    expects +incdir paths separately. We pull the `-D<macro>=<val>`
    Verilog defines too — Ibex's RVFI trace ports are gated on
    `-DRVFI=1`, and dropping them silently causes a ton of
    PINNOTFOUND errors when `ibex_top_tracing` binds its internal
    RVFI bus.

    `-DSYNTHESIS=1` is intentionally filtered: the fusesoc `lint`
    target sets it to emulate a synth context, but for our actual
    Verilator simulation we need SYNTHESIS undefined (that's what
    gates the DPI meminit + `$readmemh` helpers in prim_util)."""
    incdirs: List[str] = []
    sv_files: List[str] = []
    vlt_files: List[str] = []
    defines: List[str] = []
    for raw in vc_path.read_text().splitlines():
        line = raw.strip()
        if not line or line.startswith("#") or line.startswith("//"):
            continue
        if line.startswith("+incdir+"):
            incdirs.append(line[len("+incdir+"):])
            continue
        if line.startswith("-D"):
            body = line[2:]
            name = body.split("=", 1)[0]
            if name == "SYNTHESIS":
                continue
            defines.append(line)
            continue
        if line.startswith("-"):
            # -CFLAGS, --Mdir, etc. are irrelevant or overridden by us.
            continue
        if line.endswith(".vlt"):
            vlt_files.append(line)
        elif line.endswith((".sv", ".svh", ".v")):
            sv_files.append(line)
        # Everything else (cc files, Makefiles, ...) we ignore.
    return incdirs, sv_files, vlt_files, defines


def test_ibex_timer_isr_end_to_end(
    arch_bin: str,
    riscv_gcc: str,
    ibex_soc_filelist: dict,
    tmp_path: Path,
) -> None:
    from cocotb_tools.runner import get_runner

    # 1. RV32 program → .vmem.
    vmem = _build_sw(tmp_path / "sw_build")

    # 2. Extract filelist from the fusesoc-resolved Ibex .vc and
    #    combine with our generated + hand-written SV.
    vc_path: Path = ibex_soc_filelist["vc_path"]
    build_dir: Path = ibex_soc_filelist["build_dir"]
    extra_sv: list[Path] = ibex_soc_filelist["extra_sv"]

    incdirs, sv_files, vlt_files, defines = _parse_vc(vc_path)

    # The .vc paths are relative to the build_dir — absolutize.
    def _abs(p: str) -> str:
        pp = Path(p)
        if not pp.is_absolute():
            pp = build_dir / pp
        return str(pp.resolve())

    abs_incdirs  = [_abs(p) for p in incdirs]
    abs_sv_files = [_abs(p) for p in sv_files]
    abs_vlt_files = [_abs(p) for p in vlt_files]

    # cocotb-runner's auto file-type detection doesn't recognise `.vlt`
    # (ext compared without the leading dot), so wrap them in the
    # explicit VerilatorControlFile tag. sv/svh/.v get auto-detected.
    from cocotb_tools.runner import VerilatorControlFile
    sources = (
        [VerilatorControlFile(p) for p in abs_vlt_files]
        + abs_sv_files
        + [str(p) for p in extra_sv]
    )

    runner = get_runner("verilator")
    sim_build = tmp_path / "sim_build"

    # Verilator build. `--public-flat-rw` exposes all internal signals
    # to the VPI so cocotb can poke `dut.u_ram.u_ram.mem[idx]` directly
    # — avoids relying on Verilator's flaky `-GSRAMInitFile` string
    # parameter override.
    runner.build(
        sources=sources,
        hdl_toplevel="ibex_mini_soc",
        build_dir=str(sim_build),
        always=True,
        includes=abs_incdirs,
        build_args=[
            *defines,
            "--unroll-count", "72",
            "--public-flat-rw",
            "-Wno-IMPORTSTAR",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-UNUSEDPARAM",
            "-Wno-PINMISSING",
            "-Wno-WIDTHEXPAND",
            "-Wno-fatal",
        ],
    )

    # cocotb reads VMEM_PATH at test time to load the program into RAM.
    results_xml = runner.test(
        test_module="test_timer_isr",
        hdl_toplevel="ibex_mini_soc",
        build_dir=str(sim_build),
        test_dir=str(COCOTB_TESTS_DIR),
        results_xml=str(tmp_path / "results_timer_isr.xml"),
        extra_env={"VMEM_PATH": str(vmem)},
    )

    import xml.etree.ElementTree as ET
    tree = ET.parse(results_xml)
    root = tree.getroot()
    failures = (int(root.attrib.get("failures", "0"))
                + int(root.attrib.get("errors", "0")))
    assert failures == 0, (
        f"cocotb reported {failures} failures; see {results_xml}"
    )
