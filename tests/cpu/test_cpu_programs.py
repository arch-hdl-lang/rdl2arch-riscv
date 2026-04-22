"""End-to-end cpu/ bring-up tests: RV32 programs running on the real
Ibex + our generated CLINT + our generated PLIC under Verilator+cocotb.

Parametrized over the three M-mode interrupt paths:

  * timer_isr  — Phase 6.2: CLINT mtime/mtimecmp → mip.MTIP → cause 7
  * sw_isr     — Phase 6.3: CLINT msip write    → mip.MSIP → cause 3
  * ext_isr    — Phase 6.3: PLIC external src   → mip.MEIP → cause 11,
                 handler does claim + complete through the PLIC

Each case:
  1. Builds its RV32 program via `make -C tests/cpu/sw <prog>.vmem`.
  2. Builds the Verilator model of `ibex_mini_soc` (CLINT + PLIC +
     Ibex + RAM + simctrl), reusing the fusesoc-resolved Ibex filelist
     from the Phase-6.1 session fixture.
  3. Launches the matching cocotb test module, which pokes the .vmem
     into `u_ram.u_ram.mem[]` via VPI, releases reset, runs until the
     program writes `done_marker = 0xFEEDFACE`, and asserts the
     expected CSR / PLIC state.

The Verilator build is cached by cocotb-runner per unique `build_dir`,
so parametrized cases that reuse the same sim_build get a fast
incremental rebuild (or no rebuild at all).
"""

from __future__ import annotations

import shutil
import subprocess
import xml.etree.ElementTree as ET
from dataclasses import dataclass
from pathlib import Path
from typing import List, Tuple

import pytest


TESTS_DIR = Path(__file__).parent
SW_DIR = TESTS_DIR / "sw"
COCOTB_TESTS_DIR = TESTS_DIR / "cocotb_tests"


pytest.importorskip("cocotb_tools.runner")


# ── parametrized test programs ───────────────────────────────────────
@dataclass(frozen=True)
class CpuProgram:
    name: str                # matches the .S stem and the .vmem filename
    cocotb_module: str       # matches the `test_*.py` under cocotb_tests/

PROGRAMS: list[CpuProgram] = [
    CpuProgram(name="timer_isr",        cocotb_module="test_timer_isr"),
    CpuProgram(name="sw_isr",           cocotb_module="test_sw_isr"),
    CpuProgram(name="ext_isr",          cocotb_module="test_ext_isr"),
    CpuProgram(name="multictx_isr",     cocotb_module="test_multictx_isr"),
    CpuProgram(name="mscratch_csrfile", cocotb_module="test_mscratch_csrfile"),
    CpuProgram(name="mtvec_csrfile",    cocotb_module="test_mtvec_csrfile"),
    CpuProgram(
        name="mepc_mcause_mtval_csrfile",
        cocotb_module="test_mepc_mcause_mtval_csrfile",
    ),
    CpuProgram(
        name="mstatus_csrfile",
        cocotb_module="test_mstatus_csrfile",
    ),
    CpuProgram(
        name="mie_mip_csrfile",
        cocotb_module="test_mie_mip_csrfile",
    ),
    CpuProgram(
        name="mcountinhibit_csrfile",
        cocotb_module="test_mcountinhibit_csrfile",
    ),
    CpuProgram(
        name="mcycle_csrfile",
        cocotb_module="test_mcycle_csrfile",
    ),
    CpuProgram(
        name="debug_csrs_csrfile",
        cocotb_module="test_debug_csrs_csrfile",
    ),
    CpuProgram(
        name="hpm_csrs_csrfile",
        cocotb_module="test_hpm_csrs_csrfile",
    ),
]


# ── fixtures ────────────────────────────────────────────────────────
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


@pytest.fixture(scope="session")
def sw_build_dir(tmp_path_factory: pytest.TempPathFactory) -> Path:
    """Shared build directory for all three programs.

    `make all` builds every program listed in the Makefile's `PROGS`,
    so we can share one output tree and just pick the right .vmem per
    test case."""
    return tmp_path_factory.mktemp("cpu_sw_build")


@pytest.fixture(scope="session")
def built_programs(riscv_gcc: str, sw_build_dir: Path) -> dict[str, Path]:
    """Run `make all` once, return `{prog_name: vmem_path}`."""
    result = subprocess.run(
        ["make", "-C", str(SW_DIR), f"BUILD={sw_build_dir}", "all"],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"make failed:\nSTDERR:\n{result.stderr}\n"
            f"STDOUT:\n{result.stdout}"
        )
    vmems: dict[str, Path] = {}
    for prog in PROGRAMS:
        vmem = sw_build_dir / f"{prog.name}.vmem"
        if not vmem.is_file():
            raise RuntimeError(
                f"expected {vmem} after `make all`; not found"
            )
        vmems[prog.name] = vmem
    return vmems


# ── .vc wrangling (shared with the lint test, kept inline to avoid
#    a conftest dependency chain). ───────────────────────────────────
def _parse_vc(vc_path: Path) -> Tuple[List[str], List[str], List[str], List[str]]:
    """Split a Verilator command-file into (incdirs, sv, vlt, defines).

    cocotb-runner wants sources as a list rather than `-f`, and
    expects +incdir paths separately. We pull the `-D<macro>=<val>`
    Verilog defines too — Ibex's RVFI trace ports are gated on
    `-DRVFI=1`, and dropping them silently causes PINNOTFOUND errors
    on `ibex_top_tracing`'s internal RVFI bus.

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
            continue
        if line.endswith(".vlt"):
            vlt_files.append(line)
        elif line.endswith((".sv", ".svh", ".v")):
            sv_files.append(line)
    return incdirs, sv_files, vlt_files, defines


@pytest.fixture(scope="session")
def verilator_runner(
    ibex_soc_filelist: dict,
    tmp_path_factory: pytest.TempPathFactory,
):
    """Build the Verilator model of `ibex_mini_soc` ONCE per test session
    and return the fully-configured Runner. All three programs target
    the same SoC hardware — only the loaded RAM contents differ — so
    we reuse a single compilation.

    Returning the Runner *instance* (not just the build dir) sidesteps
    a cocotb-runner wart: creating a fresh `get_runner()` and calling
    `.test(build_dir=...)` on it raises `AttributeError` because the
    internal `_vhdl_sources`/`_verilog_sources` state was only set by
    the `.build()` call on a DIFFERENT instance."""
    from cocotb_tools.runner import VerilatorControlFile, get_runner

    vc_path: Path = ibex_soc_filelist["vc_path"]
    build_dir: Path = ibex_soc_filelist["build_dir"]
    extra_sv: list[Path] = ibex_soc_filelist["extra_sv"]

    incdirs, sv_files, vlt_files, defines = _parse_vc(vc_path)

    def _abs(p: str) -> str:
        pp = Path(p)
        if not pp.is_absolute():
            pp = build_dir / pp
        return str(pp.resolve())

    abs_incdirs   = [_abs(p) for p in incdirs]
    abs_sv_files  = [_abs(p) for p in sv_files]
    abs_vlt_files = [_abs(p) for p in vlt_files]

    sources = (
        [VerilatorControlFile(p) for p in abs_vlt_files]
        + abs_sv_files
        + [str(p) for p in extra_sv]
    )

    sim_build = tmp_path_factory.mktemp("cpu_sim_build")
    runner = get_runner("verilator")
    runner.build(
        sources=sources,
        hdl_toplevel="ibex_mini_soc",
        build_dir=str(sim_build),
        always=True,
        includes=abs_incdirs,
        build_args=[
            *defines,
            "--unroll-count", "72",
            "--public-flat-rw",    # cocotb hierarchical access to mem[*]
            "-Wno-IMPORTSTAR",
            "-Wno-UNUSEDSIGNAL",
            "-Wno-UNUSEDPARAM",
            "-Wno-PINMISSING",
            "-Wno-WIDTHEXPAND",
            # Our CsrFile / CLINT / PLIC now use Async, Low reset
            # (matches Ibex's rst_ni). Ibex's RAMs in the shared tree
            # still sample rst sync, so the same top-level IO_RST_N
            # legitimately goes into both sync and async flop
            # domains. A real SoC has a reset controller; our sim
            # flow is fine mixing them.
            "-Wno-SYNCASYNCNET",
            "-Wno-fatal",
        ],
    )
    return runner, sim_build


# ── the actual test ────────────────────────────────────────────────
@pytest.mark.parametrize("program", PROGRAMS, ids=lambda p: p.name)
def test_cpu_program(
    program: CpuProgram,
    built_programs: dict[str, Path],
    verilator_runner,
    tmp_path: Path,
) -> None:
    """Run one of the bring-up programs end-to-end on the real Ibex SoC."""
    runner, sim_build = verilator_runner
    vmem = built_programs[program.name]

    results_xml = runner.test(
        test_module=program.cocotb_module,
        hdl_toplevel="ibex_mini_soc",
        build_dir=str(sim_build),
        test_dir=str(COCOTB_TESTS_DIR),
        results_xml=str(tmp_path / f"results_{program.name}.xml"),
        extra_env={"VMEM_PATH": str(vmem)},
    )

    tree = ET.parse(results_xml)
    root = tree.getroot()
    failures = (
        int(root.attrib.get("failures", "0"))
        + int(root.attrib.get("errors", "0"))
    )
    assert failures == 0, (
        f"cocotb reported {failures} failures for {program.name}; "
        f"see {results_xml}"
    )
