"""Build-once, reuse-many pybind harness for rdl2arch-riscv sim tests.

The riscv exporter emits four `.arch` files (package + CSR file + access
controller + trap coordinator), plus this tests module generates an
integrated-top `.arch` that wires them together. `arch sim --pybind`
(arch >= v0.44, see arch-com PR #40) compiles every module's pybind
wrapper into the same physical `.so` and symlinks the remaining
`V<Module>_pybind` names to it, so a single invocation produces all
four loadable modules.

`build_all_sim` runs that single invocation and returns a
`{target: so_path}` dict. `build_sim` is a thin wrapper kept for
callers that only want one target.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from systemrdl import RDLCompiler

from rdl2arch_riscv import RiscvCsrExporter
from rdl2arch_riscv.scan_csrs import scan
from rdl2arch_riscv.udps import ALL_UDPS

from sim.integrated_top import emit_integrated_top, integrated_top_name


MODULE_SUFFIXES = {
    "csr_file":   "CsrFile",
    "access":     "CsrAccess",
    "trap_coord": "CsrTrapCoord",
}


def _compile_rdl(rdl_path: Path):
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(rdl_path))
    return rdlc.elaborate()


def build_all_sim(rdl_path: Path, out_dir: Path, arch_bin: str) -> dict[str, str]:
    """Build every pybind target for `rdl_path` in a single invocation.

    Returns a `{target: so_path}` dict with keys:
      * `csr_file`, `access`, `trap_coord`  — one per emitted module
      * `integrated`                        — the test-only wrapper top

    The emitter output and the generated integrated top are written under
    `out_dir`; the compiled `.so` files land in `out_dir/sim`.
    """
    root = _compile_rdl(rdl_path)
    RiscvCsrExporter().export(root.top, str(out_dir))

    design = scan(root.top, xlen=32)
    top_name = integrated_top_name(design)
    (out_dir / f"{top_name}.arch").write_text(emit_integrated_top(design))

    build_dir = out_dir / "sim"
    arch_files = sorted(out_dir.glob("*.arch"))
    result = subprocess.run(
        [arch_bin, "sim", "--pybind", "-o", str(build_dir),
         *[str(p) for p in arch_files]],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            "arch sim --pybind failed:\n"
            f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )

    # Map each target to its pybind .so. Module names follow
    # `V<Base><Suffix>_pybind` for the emitter's three modules and
    # `V<integrated_top_name>_pybind` for the test wrapper.
    base = design.module_name[: -len("CsrFile")]
    target_module_names = {
        "csr_file":   f"{base}CsrFile",
        "access":     f"{base}CsrAccess",
        "trap_coord": f"{base}CsrTrapCoord",
        "integrated": top_name,
    }
    out: dict[str, str] = {}
    for target, mod_name in target_module_names.items():
        wanted = f"V{mod_name}_pybind"
        matches = [p for p in build_dir.glob(f"{wanted}.*.so")]
        if not matches:
            raise RuntimeError(
                f"no pybind .so for target={target} (expected {wanted}) in {build_dir}"
            )
        # If there's both a real file and a symlink, pick either — they
        # resolve to the same shared library.
        out[target] = str(matches[0])
    return out


def build_sim(rdl_path: Path, target: str, out_dir: Path, arch_bin: str) -> str:
    """Backward-compatible single-target variant of `build_all_sim`."""
    if target not in MODULE_SUFFIXES and target != "integrated":
        raise ValueError(
            f"target must be one of {list(MODULE_SUFFIXES) + ['integrated']}; "
            f"got {target!r}"
        )
    return build_all_sim(rdl_path, out_dir, arch_bin)[target]


def fresh_dut(so_path: str):
    """Load the pybind .so and instantiate the DUT class.

    Uses importlib so sibling fixtures that share a module name (e.g. two
    different RDL fixtures both emitting a `MTrapCsrs*` suffix) don't
    collide in `sys.modules`.
    """
    so = Path(so_path)
    mod_name = so.name.split(".")[0]
    sys.modules.pop(mod_name, None)
    spec = importlib.util.spec_from_file_location(mod_name, so_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"cannot load pybind module from {so_path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[mod_name] = module
    spec.loader.exec_module(module)
    cls_name = mod_name.replace("_pybind", "")
    return getattr(module, cls_name)()
