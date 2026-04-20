"""Top-level exporter: scan, validate, emit CSR package + CSR file + access controller."""

import os
from typing import Optional, Union

from systemrdl.node import AddrmapNode, RootNode

from .emit_access_controller import emit_access_controller
from .emit_csr_file import emit_csr_file
from .emit_csr_package import emit_package
from .scan_csrs import scan
from .validate_csrs import validate


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
        """Emit the Phase 1+2 outputs for a SystemRDL CSR spec.

        Writes three .arch files to output_dir:
          <package_name>.arch      — shared types, CSR addr enum, hwif structs.
          <module_name>.arch       — the CSR file module (storage + decode).
          <access_name>.arch       — the pure-combinational access controller
                                     (privilege + read-only check).

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

        # Access-controller module shares the CSR file's base name with an
        # `Access` suffix, matching the "<Name>CsrFile" / "<Name>CsrAccess"
        # naming convention in the plan (§6.3).
        base = design.module_name
        if base.endswith("CsrFile"):
            access_name = base[: -len("CsrFile")] + "CsrAccess"
        else:
            access_name = base + "Access"

        pkg_src    = emit_package(design)
        csr_src    = emit_csr_file(design)
        access_src = emit_access_controller(design, access_name)

        os.makedirs(output_dir, exist_ok=True)
        pkg_path    = os.path.join(output_dir, f"{design.package_name}.arch")
        csr_path    = os.path.join(output_dir, f"{base}.arch")
        access_path = os.path.join(output_dir, f"{access_name}.arch")
        with open(pkg_path, "w") as fh:
            fh.write(pkg_src)
        with open(csr_path, "w") as fh:
            fh.write(csr_src)
        with open(access_path, "w") as fh:
            fh.write(access_src)

        return {
            f"{design.package_name}.arch": pkg_path,
            f"{base}.arch":                csr_path,
            f"{access_name}.arch":         access_path,
        }
