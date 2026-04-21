"""Top-level PLIC exporter.

Composes two generators, same pattern as `RiscvClintExporter`:

  1. `rdl2arch.ArchExporter` — emits the MMIO register block (priority
     array, pending bitmap, enable bitmap, threshold, claim) from the
     RDL fixture. Handles cpuif (AXI4-Lite / APB4), address decode,
     hwif structs.

  2. `emit_plic_logic` (this package) — emits the sibling `PlicLogic`
     module that computes priority arbitration and drives `meip_out`.

Three `.arch` files per fixture:

    <module_name>Pkg.arch      — from rdl2arch: enum, per-reg structs, hwif structs.
    <module_name>.arch         — from rdl2arch: MMIO register block.
    <module_name>Logic.arch    — from this module: arbitration + claim + meip_out.
"""

from __future__ import annotations

import os
from typing import Optional, Type, Union

from systemrdl.node import AddrmapNode, RootNode

from rdl2arch import ArchExporter
from rdl2arch.cpuif.base import CpuifBase
from rdl2arch.cpuif.axi4lite import AXI4Lite_Cpuif

from .emit_plic_logic import emit_plic_logic, scan_plic


def _camel(snake: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in snake.split("_") if p)


class RiscvPlicExporter:
    def export(
        self,
        node: Union[RootNode, AddrmapNode],
        output_dir: str,
        *,
        cpuif_cls: Type[CpuifBase] = AXI4Lite_Cpuif,
        module_name: Optional[str] = None,
        package_name: Optional[str] = None,
    ) -> dict[str, str]:
        """Emit the PLIC register block + logic module.

        Returns {filename: absolute path}, containing:
          <module>Pkg.arch     — shared types
          <module>.arch        — MMIO register block
          <module>Logic.arch   — arbiter + meip driver
        """
        top = node.top if isinstance(node, RootNode) else node

        mod = module_name or _camel(top.inst_name)
        pkg = package_name or (mod + "Pkg")
        logic_name = mod + "Logic"

        os.makedirs(output_dir, exist_ok=True)

        # (1) Register block via generic rdl2arch.
        rb_files = ArchExporter().export(
            top, output_dir,
            cpuif_cls=cpuif_cls,
            module_name=mod,
            package_name=pkg,
        )

        # (2) Logic module.
        model = scan_plic(top, module_name=mod, package_name=pkg)
        logic_src = emit_plic_logic(model, logic_name)
        logic_path = os.path.join(output_dir, f"{logic_name}.arch")
        with open(logic_path, "w") as fh:
            fh.write(logic_src)

        out: dict[str, str] = dict(rb_files)
        out[f"{logic_name}.arch"] = logic_path
        return out
