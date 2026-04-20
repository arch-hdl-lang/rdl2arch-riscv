"""Build-once, reuse-many pybind harness for rdl2arch-riscv sim tests.

The riscv exporter emits four `.arch` files (package + CSR file + access
controller + trap coordinator). Each sim build targets one module: the
package is always included so struct types resolve, plus the specific
module `.arch` file under test.

The arch `--pybind` flow only binds struct types a module actually
references (see arch-com PR #32), so module-isolated builds Just Work
as long as the package is passed alongside.
"""

from __future__ import annotations

import importlib.util
import subprocess
import sys
from pathlib import Path

from systemrdl import RDLCompiler

from rdl2arch_riscv import RiscvCsrExporter
from rdl2arch_riscv.udps import ALL_UDPS


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


def build_sim(rdl_path: Path, target: str, out_dir: Path, arch_bin: str) -> str:
    """Emit ARCH, build `arch sim --pybind` for one module, return the .so path.

    `target` must be one of `csr_file`, `access`, `trap_coord`.
    """
    if target not in MODULE_SUFFIXES:
        raise ValueError(f"target must be one of {list(MODULE_SUFFIXES)}; got {target!r}")
    suffix = MODULE_SUFFIXES[target]

    root = _compile_rdl(rdl_path)
    RiscvCsrExporter().export(root.top, str(out_dir))

    # The package is named <Base>CsrFilePkg; the target module is <Base><suffix>.
    pkg_files = sorted(out_dir.glob("*Pkg.arch"))
    mod_files = sorted(p for p in out_dir.glob(f"*{suffix}.arch")
                       if not p.name.endswith("Pkg.arch"))
    if len(pkg_files) != 1 or len(mod_files) != 1:
        emitted = sorted(p.name for p in out_dir.glob("*.arch"))
        raise RuntimeError(
            f"expected exactly one package + one {suffix} module, "
            f"found pkg={pkg_files}, mod={mod_files}, all={emitted}"
        )

    build_dir = out_dir / f"sim_{target}"
    result = subprocess.run(
        [arch_bin, "sim", "--pybind", "-o", str(build_dir),
         str(pkg_files[0]), str(mod_files[0])],
        capture_output=True, text=True,
    )
    if result.returncode != 0:
        raise RuntimeError(
            f"arch sim --pybind failed for target={target}:\n"
            f"STDERR:\n{result.stderr}\nSTDOUT:\n{result.stdout}"
        )

    so_files = list(build_dir.glob("V*_pybind.*.so"))
    if not so_files:
        raise RuntimeError(f"No pybind .so in {build_dir}")
    return str(so_files[0])


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
