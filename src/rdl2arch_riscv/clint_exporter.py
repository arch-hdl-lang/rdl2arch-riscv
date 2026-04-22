"""Top-level CLINT exporter.

Composes two generators:

  1. `rdl2arch.ArchExporter` — emits the MMIO register block (package +
     module) from the RDL fixture. Handles cpuif (AXI4-Lite / APB4),
     address decode, hwif struct, sw-side semantics.

  2. `emit_clint_logic` (this package) — emits the sibling `ClintLogic`
     module that wires the register block's hwif_out/in to the CPU's
     `mip.msip` / `mip.mtip` bits and drives the 64-bit mtime counter.

Three `.arch` files are produced per fixture:

    <module_name>Pkg.arch      — from rdl2arch: enum, per-reg structs, hwif structs.
    <module_name>.arch         — from rdl2arch: MMIO register block.
    <module_name>Logic.arch    — from this module: timer + msip/mtip fanout.
"""

from __future__ import annotations

import os
from typing import Optional, Type, Union

from systemrdl.node import AddrmapNode, RootNode

from rdl2arch import ArchExporter, ResetStyle
from rdl2arch.cpuif.base import CpuifBase
from rdl2arch.cpuif.axi4lite import AXI4Lite_Cpuif

from .emit_clint_logic import emit_clint_logic, scan_clint


def _camel(snake: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in snake.split("_") if p)


class RiscvClintExporter:
    def export(
        self,
        node: Union[RootNode, AddrmapNode],
        output_dir: str,
        *,
        cpuif_cls: Type[CpuifBase] = AXI4Lite_Cpuif,
        module_name: Optional[str] = None,
        package_name: Optional[str] = None,
    ) -> dict[str, str]:
        """Emit the CLINT register block + logic module.

        Returns a mapping of filename → absolute path, containing:
          <module>Pkg.arch     — shared types
          <module>.arch        — MMIO register block
          <module>Logic.arch   — timer + passthrough logic
        """
        top = node.top if isinstance(node, RootNode) else node

        mod  = module_name  or _camel(top.inst_name)
        pkg  = package_name or (mod + "Pkg")
        logic_name = mod + "Logic"

        os.makedirs(output_dir, exist_ok=True)

        # (1) Delegate the register block to the generic rdl2arch exporter.
        # Async-low reset matches the rest of the rdl2arch-riscv stack
        # (CsrFile / TrapCoord) + Ibex's `rst_ni` convention, so the
        # whole SoC presents a single reset shape and doesn't need
        # per-device polarity inversions at the wiring level.
        rb_files = ArchExporter().export(
            top, output_dir,
            cpuif_cls=cpuif_cls,
            module_name=mod,
            package_name=pkg,
            reset_style=ResetStyle.ASYNC_LOW,
        )

        # (2) Scan + emit the sibling logic module.
        model = scan_clint(top, module_name=mod, package_name=pkg)
        logic_src = emit_clint_logic(model, logic_name)
        logic_path = os.path.join(output_dir, f"{logic_name}.arch")
        with open(logic_path, "w") as fh:
            fh.write(logic_src)

        out: dict[str, str] = dict(rb_files)
        out[f"{logic_name}.arch"] = logic_path
        return out
