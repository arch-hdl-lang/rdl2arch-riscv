"""Drive Yosys synthesis of the Ibex hybrid SoC against Nangate45.

Reuses the file-list machinery in `tests/cpu/conftest.py` so the synth
flow consumes the same SV that the cocotb tests verify — no parallel
list to drift.

Usage:
    make synth                      # default; full SoC, top = ibex_mini_soc
    make synth-ibex-top             # core-only synth; matches paper claim
    NANGATE45_ROOT=/path make synth # override PDK location

Env:
    NANGATE45_ROOT  Path to the Nangate45 platform (must contain
                    `lib/NangateOpenCellLibrary_typical.lib`). Default:
                    $HOME/pdks/nangate45.
    IBEX_ROOT       lowRISC/ibex checkout. Default: $HOME/github/ibex.
    ARCH_BIN        Path to the `arch` binary. Default: looked up in the
                    same places `tests/conftest.py:find_arch_binary` checks.
    SYNTH_TOP       Top-module name. Default: ibex_mini_soc.
    SYNTH_BUILD_DIR Output directory. Default: tests/synth/build/.

Output:
    {build_dir}/synth.json           — Yosys netlist (post-abc gate level)
    {build_dir}/synth.stat           — `stat -liberty …` report (cell counts)
    {build_dir}/synth.log            — full Yosys stdout / stderr
    {build_dir}/synth.f              — file list passed to Yosys
"""

from __future__ import annotations

import argparse
import os
import shutil
import subprocess
import sys
from pathlib import Path

# Make the conftest helpers importable. They live in `tests/cpu/conftest.py`
# alongside the cocotb tests; their internal helpers are pytest-free even
# though the public fixtures aren't.
#
# Both `tests/conftest.py` and `tests/cpu/conftest.py` claim the module
# name `conftest` under pytest, and the cpu one does
# `from conftest import RDL_DIR` — which would self-reference if we
# loaded it under that name. Workaround: pre-load the top-level conftest
# as `conftest`, then load the cpu one under a distinct module name.
TESTS_DIR = Path(__file__).resolve().parents[1]
sys.path.insert(0, str(TESTS_DIR))    # parent conftest's RDL_DIR import path

import importlib.util

def _load_module(name: str, path: Path):
    spec = importlib.util.spec_from_file_location(name, path)
    assert spec is not None and spec.loader is not None
    mod = importlib.util.module_from_spec(spec)
    sys.modules[name] = mod
    spec.loader.exec_module(mod)
    return mod

# `conftest` (parent) MUST be in sys.modules before we exec the cpu
# conftest — its `from conftest import RDL_DIR` resolves through
# sys.modules.
_load_module("conftest", TESTS_DIR / "conftest.py")
ibex_cpu_conftest = _load_module(
    "ibex_cpu_conftest", TESTS_DIR / "cpu" / "conftest.py"
)


# --- env helpers ---------------------------------------------------------


def _resolve_nangate45_root() -> Path:
    p = Path(os.environ.get("NANGATE45_ROOT", str(Path.home() / "pdks" / "nangate45")))
    lib = p / "lib" / "NangateOpenCellLibrary_typical.lib"
    if not lib.is_file():
        sys.exit(
            f"error: NANGATE45 lib not found at {lib}\n"
            f"       Install with the steps in tests/synth/README.md, or set\n"
            f"       NANGATE45_ROOT to a checkout that contains "
            f"flow/platforms/nangate45/."
        )
    return p


def _resolve_ibex_root() -> Path:
    env = os.environ.get("IBEX_ROOT")
    p = Path(env).expanduser() if env else (Path.home() / "github" / "ibex")
    if not p.is_dir():
        sys.exit(
            f"error: Ibex checkout not found at {p}.\n"
            f"       git clone https://github.com/lowRISC/ibex {p}\n"
            f"       or set IBEX_ROOT=/path/to/ibex."
        )
    return p


def _resolve_arch_bin() -> str:
    env = os.environ.get("ARCH_BIN")
    if env and Path(env).is_file():
        return env
    sibling = Path.home() / "github" / "arch-com" / "target" / "release" / "arch"
    if sibling.is_file():
        return str(sibling)
    which = shutil.which("arch")
    # macOS /usr/bin/arch is the system arch(1), not our compiler.
    if which and which != "/usr/bin/arch":
        return which
    sys.exit("error: `arch` compiler not found. Build arch-com or set ARCH_BIN.")


def _require(tool: str) -> str:
    p = shutil.which(tool)
    if p is None:
        sys.exit(f"error: `{tool}` not on PATH.")
    return p


# --- file-list build -----------------------------------------------------


# Shared sim-only files that fusesoc lists but synth tools can't
# (or shouldn't) consume. Skip them at the file-list stage — the
# design's references to them are top-level testbench-only and don't
# appear in `ibex_top` / `ibex_mini_soc` synthesis paths.
_SV_SKIP_FOR_SYNTH = {
    # $fwrite / $finish — sim controller for stop-on-fault.
    "simulator_ctrl.sv",
    # Disassembly tracer; SystemVerilog string ops sv2v can't handle and
    # synth tools wouldn't elaborate anyway. Gated by SYNTHESIS in
    # consumers, but sv2v parses before macro evaluation so we drop
    # the file outright.
    "ibex_tracer.sv",
    "ibex_tracer_pkg.sv",
    # `ibex_top_tracing` is `ibex_top` wrapped with the tracer — once we
    # drop the tracer the wrapper has nothing to do. Synth flows go
    # straight at `ibex_top` instead.
    "ibex_top_tracing.sv",
}


def _strip_for_synth(stripped_vc_text: str) -> tuple[list[str], list[Path]]:
    """Parse the (already verilator-stripped) .vc and split into
    (yosys_flags, sv_files).

    Yosys's `read_verilog` uses Verilog-tool standard flag syntax
    (`-DNAME=VAL`, `-Ipath`) — not Verilator's `+define+NAME=VAL` /
    `+incdir+path`. We translate as we parse so the file written to
    `synth.f` is directly usable by `read_verilog`.

    Sim-only paths in `_SV_SKIP_FOR_SYNTH` are dropped at this stage.
    """
    flags: list[str] = []
    files: list[Path] = []
    for line in stripped_vc_text.splitlines():
        s = line.strip()
        if not s or s.startswith("//") or s.startswith("#"):
            continue
        if s.startswith("+define+"):
            flags.append("-D" + s[len("+define+"):])
            continue
        if s.startswith("-D"):
            flags.append(s)        # already in yosys form
            continue
        if s.startswith("+incdir+"):
            flags.append("-I" + s[len("+incdir+"):])
            continue
        if s.startswith("-I"):
            flags.append(s)
            continue
        if s.startswith("-"):
            # Verilator-only flags (e.g. `-Wno-fatal`, `-LDFLAGS …`).
            # Drop silently — yosys's read_verilog wouldn't accept them.
            continue
        # Plain file path. Only keep Verilog/SystemVerilog sources;
        # fusesoc's .vc lists C++ test harness, .vlt lint configs, and
        # other tool-specific assets that yosys doesn't parse.
        p = Path(s)
        if p.name in _SV_SKIP_FOR_SYNTH:
            continue
        if p.suffix.lower() not in {".sv", ".v", ".svh", ".vh"}:
            continue
        files.append(p)
    return flags, files


def build_filelist(*, build_dir: Path) -> dict:
    """Produce the SV file list + Yosys read_verilog flags.

    Mirrors `tests/cpu/conftest.py:ibex_soc_filelist` but free of pytest
    machinery so this script and the Makefile can drive it directly.
    """
    arch_bin = _resolve_arch_bin()
    ibex_root = _resolve_ibex_root()
    fusesoc_bin = _require("fusesoc")

    build_dir = build_dir.resolve()
    build_dir.mkdir(parents=True, exist_ok=True)

    # 1. Generate CLINT + PLIC + CsrFile via the same exporters the
    #    pytest fixture uses. Same RDL fixtures, same arch-build chain.
    generated_dir = build_dir / "generated"
    gen_sv = ibex_cpu_conftest._generate_clint_plic_sv(arch_bin, generated_dir)

    # 2. fusesoc → .vc. Strip verilator-isms.
    vc_path = ibex_cpu_conftest._fusesoc_setup(ibex_root, build_dir, fusesoc_bin)
    stripped_text = ibex_cpu_conftest._strip_top_and_exe(vc_path)

    # 3. Pull our hand-written SoC glue from tests/cpu/soc/.
    soc_dir = TESTS_DIR / "cpu" / "soc"
    soc_sv = [
        soc_dir / "obi_to_axi_lite.sv",
        soc_dir / "ibex_mini_soc.sv",
        soc_dir / "ibex_cs_registers_hybrid.sv",
    ]

    # 4. Ibex shared sim helpers (RAM model + sim controller). The sim
    #    controller is in `_SV_SKIP_FOR_SYNTH` and gets dropped; the
    #    RAM model elaborates as a behavioural model that Yosys treats
    #    as inferred memory.
    shared_sv = [
        ibex_root / "shared" / "rtl" / "ram_2p.sv",
        ibex_root / "shared" / "rtl" / "sim" / "simulator_ctrl.sv",
    ]
    for p in shared_sv:
        if not p.is_file():
            sys.exit(f"error: Ibex shared SV missing at {p}")

    flags, fusesoc_files = _strip_for_synth(stripped_text)
    # Strip sim-only defines that pull in non-synthesizable RTL chunks.
    # RVFI (RISC-V Formal Interface) injects dotted cross-module
    # references (`id_stage_i.controller_i.rvfi_flush_next` etc.) used
    # by formal tools — yosys can't resolve them.
    _SYNTH_HOSTILE_DEFINES = ("RVFI",)
    flags = [
        f for f in flags
        if not (f.startswith("-D") and any(
            f[2:].split("=", 1)[0] == d for d in _SYNTH_HOSTILE_DEFINES
        ))
    ]
    # Add `SYNTHESIS` so Ibex's behavioural blocks switch to the
    # synth-friendly variants where present.
    if not any(f == "-DSYNTHESIS" or f.startswith("-DSYNTHESIS=") for f in flags):
        flags.append("-DSYNTHESIS")

    # The .vc paths are relative to wherever fusesoc resolved them
    # (parent of the .vc file). Resolve them to absolute paths now so
    # Yosys doesn't care about its own cwd. Same for include dirs.
    vc_root = vc_path.parent.resolve()
    fusesoc_files = [
        (vc_root / p).resolve() if not p.is_absolute() else p
        for p in fusesoc_files
    ]
    flags = [
        ("-I" + str((vc_root / f[2:]).resolve())
         if f.startswith("-I") and not Path(f[2:]).is_absolute() else f)
        for f in flags
    ]

    # `gen_sv`, `soc_sv`, `shared_sv` come *after* the fusesoc list so
    # our forks (e.g. `ibex_cs_registers_hybrid.sv`) are read after any
    # upstream stub. Order doesn't strictly matter for read_verilog but
    # matches the verilator file ordering for parity.
    files: list[Path] = (
        list(fusesoc_files)
        + list(gen_sv)
        + [p for p in soc_sv if p.name not in _SV_SKIP_FOR_SYNTH]
        + [p for p in shared_sv if p.name not in _SV_SKIP_FOR_SYNTH]
    )

    return {
        "files": files,
        "flags": flags,
        "build_dir": build_dir,
    }


# --- Yosys driver --------------------------------------------------------


def run_sv2v(
    *,
    top: str,
    files: list[Path],
    flags: list[str],
    out_path: Path,
    log_path: Path,
) -> None:
    """Translate SystemVerilog 2017 → Verilog 2005 with sv2v.

    Yosys's native SV parser is incomplete (no `'(...)` casts among
    other gaps); sv2v is the canonical workaround.

    `--top` prunes uninstantiated modules from the output. Critical
    here because fusesoc's filelist pulls in the whole `prim_*` library
    (USB diff RX, etc.) including modules that use Verilog-2001 drive-
    strength `assign (weak0, pull1)` syntax that Yosys's V2005 frontend
    doesn't accept. Pruning to just what `top` actually instantiates
    avoids parsing those unreachable modules.
    """
    sv2v_bin = _require("sv2v")
    cmd = [sv2v_bin, f"--top={top}", "--write=" + str(out_path)]
    for f in flags:
        if f.startswith("-D"):
            cmd.append("-D" + f[2:])
        elif f.startswith("-I"):
            cmd.append("-I" + f[2:])
    cmd.extend(str(p) for p in files)
    proc = subprocess.run(cmd, capture_output=True, text=True)
    log_path.write_text(
        "$ " + " ".join(cmd) + "\n\n"
        + proc.stdout
        + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else "")
    )
    if proc.returncode != 0:
        sys.exit(
            f"error: sv2v failed (exit {proc.returncode}). Log: {log_path}\n"
            + (proc.stderr[-2000:] if proc.stderr else "")
        )
    if not out_path.is_file():
        sys.exit(f"error: sv2v produced no output at {out_path}; see {log_path}")


def run_yosys(
    *,
    top: str,
    build_dir: Path,
    nangate45_root: Path,
    files: list[Path],
    flags: list[str],
) -> None:
    yosys_bin = _require("yosys")

    # Preprocess SV → V via sv2v.
    merged_v = build_dir / "merged.v"
    sv2v_log = build_dir / "sv2v.log"
    print(f"\nRunning sv2v on {len(files)} files → {merged_v}")
    run_sv2v(top=top, files=files, flags=flags, out_path=merged_v, log_path=sv2v_log)
    print(f"  sv2v log: {sv2v_log}")
    print(f"  merged:   {merged_v} ({merged_v.stat().st_size // 1024} KiB)")

    lib = nangate45_root / "lib" / "NangateOpenCellLibrary_typical.lib"
    json_out = build_dir / "synth.json"
    stat_out = build_dir / "synth.stat"
    log_out = build_dir / "synth.log"

    yosys_cmds = [
        f"read_verilog {merged_v}",
        f"hierarchy -top {top} -check",
        # Standard generic-tech synth flow.
        "proc",
        "flatten",
        "opt",
        "fsm",
        "opt",
        "memory",
        "opt",
        "techmap",
        "opt",
        # Map flops + combinational logic to Nangate45 cells.
        f"dfflibmap -liberty {lib}",
        f"abc -liberty {lib}",
        "clean",
        # Reports. `tee -o file <cmd>` runs cmd and writes its output to
        # `file` (yosys 0.64 — `stat` doesn't have its own -o flag).
        f"tee -o {stat_out} stat -liberty {lib}",
        f"write_json {json_out}",
    ]

    # `-s -` reads from stdin so we can pipe a multi-line command list
    # without quoting hell on the shell.
    proc = subprocess.run(
        [yosys_bin, "-s", "/dev/stdin"],
        input="\n".join(yosys_cmds) + "\n",
        text=True,
        capture_output=True,
        cwd=str(build_dir),
    )
    log_out.write_text(proc.stdout + ("\n--- stderr ---\n" + proc.stderr if proc.stderr else ""))
    if proc.returncode != 0:
        sys.exit(
            f"error: yosys failed (exit {proc.returncode}). Full log:\n"
            f"  {log_out}\n"
            f"--- last 40 lines of stdout ---\n"
            + "\n".join(proc.stdout.splitlines()[-40:])
            + (f"\n--- stderr ---\n{proc.stderr}" if proc.stderr else "")
        )

    print(f"\nYosys synth OK. Reports in {build_dir}:")
    print(f"  netlist: {json_out}")
    print(f"  stats:   {stat_out}")
    print(f"  log:     {log_out}")
    if stat_out.is_file():
        # Pull the headline numbers out of `stat`'s output for the
        # console summary.
        text = stat_out.read_text()
        for line in text.splitlines():
            line = line.strip()
            if line.startswith(("Number of cells", "Chip area", "Number of wires")):
                print(f"  {line}")


# --- entry point ---------------------------------------------------------


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__,
                                formatter_class=argparse.RawDescriptionHelpFormatter)
    p.add_argument("--top", default=os.environ.get("SYNTH_TOP", "ibex_mini_soc"),
                   help="Top module to synth (default: ibex_mini_soc).")
    p.add_argument("--build-dir",
                   default=os.environ.get("SYNTH_BUILD_DIR",
                                          str(Path(__file__).parent / "build")),
                   help="Output directory.")
    args = p.parse_args(argv)

    build_dir = Path(args.build_dir).resolve()
    nangate45_root = _resolve_nangate45_root()

    print(f"Synth target: {args.top}")
    print(f"Build dir:    {build_dir}")
    print(f"Nangate45:    {nangate45_root}")

    fl = build_filelist(build_dir=build_dir)
    print(f"\n{len(fl['files'])} SV files; {len(fl['flags'])} flags.")
    run_yosys(
        top=args.top,
        build_dir=build_dir,
        nangate45_root=nangate45_root,
        files=fl["files"],
        flags=fl["flags"],
    )
    return 0


if __name__ == "__main__":
    sys.exit(main())
