"""Top-level exporter: scan, validate, emit CSR package + CSR file."""

import os
from typing import Optional, Union

from systemrdl.node import AddrmapNode, RootNode

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
        """Emit `<Name>CsrFile.arch` + `<Name>CsrFilePkg.arch`.

        Phase-1 scope: CSR file module only. Access controller and trap
        coordinator come in later phases.
        """
        top = node.top if isinstance(node, RootNode) else node

        design = scan(
            top,
            module_name=module_name,
            package_name=package_name,
            xlen=xlen,
        )
        validate(design)

        pkg_src = emit_package(design)
        csr_src = emit_csr_file(design)

        os.makedirs(output_dir, exist_ok=True)
        pkg_path = os.path.join(output_dir, f"{design.package_name}.arch")
        csr_path = os.path.join(output_dir, f"{design.module_name}.arch")
        with open(pkg_path, "w") as fh:
            fh.write(pkg_src)
        with open(csr_path, "w") as fh:
            fh.write(csr_src)

        return {
            f"{design.package_name}.arch": pkg_path,
            f"{design.module_name}.arch": csr_path,
        }
