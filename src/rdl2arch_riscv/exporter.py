"""Top-level exporter: scan, validate, emit CSR package + CSR file + access
controller + trap coordinator."""

import os
from typing import Optional, Union

from systemrdl.node import AddrmapNode, RootNode

from .emit_access_controller import emit_access_controller
from .emit_csr_file import emit_csr_file
from .emit_csr_package import emit_package
from .emit_trap_coordinator import emit_trap_coordinator
from .scan_csrs import scan
from .validate_csrs import validate


def _sibling_name(base: str, from_suffix: str, to_suffix: str) -> str:
    """Replace `base`'s trailing `from_suffix` with `to_suffix`; if there's
    no match, just append `to_suffix`. Keeps module names in lockstep under
    the `<Name>CsrFile` / `<Name>CsrAccess` / `<Name>CsrTrapCoord`
    convention from plan §6."""
    if base.endswith(from_suffix):
        return base[: -len(from_suffix)] + to_suffix
    return base + to_suffix


class RiscvCsrExporter:
    def export(
        self,
        node: Union[RootNode, AddrmapNode],
        output_dir: str,
        *,
        module_name: Optional[str] = None,
        package_name: Optional[str] = None,
        xlen: int = 32,
    ) -> dict[str, str]:
        """Emit the Phase 1+2+3 outputs for a SystemRDL CSR spec.

        Writes four .arch files to output_dir:
          <package_name>.arch       — shared types.
          <module_name>.arch        — CSR file (storage + decode + WPRI/WARL).
          <access_name>.arch        — access controller (privilege check).
          <trap_coord_name>.arch    — trap coordinator (save_on_trap routing).

        Returns a mapping of filename → absolute path.
        """
        top = node.top if isinstance(node, RootNode) else node

        design = scan(
            top,
            module_name=module_name,
            package_name=package_name,
            xlen=xlen,
        )
        validate(design)

        base = design.module_name
        access_name     = _sibling_name(base, "CsrFile", "CsrAccess")
        trap_coord_name = _sibling_name(base, "CsrFile", "CsrTrapCoord")

        pkg_src        = emit_package(design)
        csr_src        = emit_csr_file(design)
        access_src     = emit_access_controller(design, access_name)
        trap_coord_src = emit_trap_coordinator(design, trap_coord_name)

        os.makedirs(output_dir, exist_ok=True)
        paths = {
            f"{design.package_name}.arch": (design.package_name, pkg_src),
            f"{base}.arch":                (base, csr_src),
            f"{access_name}.arch":         (access_name, access_src),
            f"{trap_coord_name}.arch":     (trap_coord_name, trap_coord_src),
        }
        out: dict[str, str] = {}
        for fname, (_, src) in paths.items():
            fullpath = os.path.join(output_dir, fname)
            with open(fullpath, "w") as fh:
                fh.write(src)
            out[fname] = fullpath
        return out
