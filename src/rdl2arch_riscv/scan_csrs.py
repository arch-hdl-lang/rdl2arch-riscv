"""Walk an elaborated RDL tree tagged with RISC-V UDPs and produce a flat
design model the emitter consumes."""

from dataclasses import dataclass, field
from typing import Optional

from systemrdl.node import AddrmapNode, FieldNode, RegNode
from systemrdl.rdltypes import OnReadType, OnWriteType

from rdl2arch import dereferencer as deref

from .udps.warl import parse_warl


@dataclass
class CsrFieldModel:
    node: FieldNode
    name: str                     # identifier inside the register struct
    msb: int
    lsb: int
    width: int
    sw_readable: bool
    sw_writable: bool
    hw_readable: bool             # exposed via hwif_out (hw reads field state)
    hw_writable: bool             # exposed via hwif_in (hw drives field state)
    reset: int
    onwrite: Optional[OnWriteType]
    onread: Optional[OnReadType]
    # RISC-V UDPs
    wpri: bool = False
    warl: Optional[tuple] = None  # ('mask', int) | ('enum', [int, ...]) | None
    priv: Optional[str] = None
    trap_signal: Optional[str] = None
    save_on_trap: bool = False
    restore_on_ret: bool = False
    hw_mirror: bool = False
    hw_increment_when: Optional[str] = None
    hw_increment_high_of: Optional[str] = None


@dataclass
class CsrRegModel:
    node: RegNode
    name: str                     # flat identifier, e.g. "mstatus"
    state_name: str               # `<name>_r`
    struct_name: str              # e.g. "MstatusReg"
    enum_variant: str             # e.g. "Mstatus"
    address: int                  # RISC-V CSR address (12-bit)
    regwidth: int                 # in bits
    fields: list[CsrFieldModel] = field(default_factory=list)
    priv: Optional[str] = None
    trap_signal: Optional[str] = None


@dataclass
class CsrDesignModel:
    top: AddrmapNode
    module_name: str              # ARCH module name for the CSR file
    package_name: str             # ARCH package name
    hwif_in_struct: str
    hwif_out_struct: str
    csr_enum_name: str
    csr_file_bus: str             # bus type: CSR file's pipeline interface
    xlen: int                     # RV32 → 32, RV64 → 64
    regs: list[CsrRegModel] = field(default_factory=list)


def scan(top: AddrmapNode, *, module_name: Optional[str] = None,
         package_name: Optional[str] = None, xlen: int = 32) -> CsrDesignModel:
    top_name = top.inst_name
    mod = module_name or (_camel(top_name) + "CsrFile")
    pkg = package_name or (mod + "Pkg")

    regs: list[CsrRegModel] = []
    for reg in _walk_regs(top):
        regs.append(_scan_reg(reg, top))

    # Module-name convention: CSR-file module is `<Base>CsrFile`. Strip that
    # suffix to build sibling names for the buses so they key off the design
    # rather than one module's name.
    base = mod[: -len("CsrFile")] if mod.endswith("CsrFile") else mod
    return CsrDesignModel(
        top=top,
        module_name=mod,
        package_name=pkg,
        hwif_in_struct=mod + "HwifIn",
        hwif_out_struct=mod + "HwifOut",
        csr_enum_name=mod + "Addr",
        csr_file_bus=base + "CsrFileBus",
        xlen=xlen,
        regs=regs,
    )


def _walk_regs(node):
    for child in node.children(unroll=False):
        if isinstance(child, RegNode):
            yield child
        elif hasattr(child, "children"):
            yield from _walk_regs(child)


def _scan_reg(reg: RegNode, top: AddrmapNode) -> CsrRegModel:
    # Prefer the explicit `riscv_csr_addr` UDP when present — that's the
    # RISC-V spec address (e.g. 0x300 for mstatus) with no shift games.
    # Fall back to the legacy convention of `csr_addr << 2` in RDL byte
    # space (dividing by 4 here), which sidesteps the RDL byte-range
    # overlap check for adjacent 12-bit CSR addresses like 0x342 + 0x343.
    csr_addr_override = reg.get_property("riscv_csr_addr")
    if csr_addr_override is not None:
        csr_addr = int(csr_addr_override)
    else:
        byte_addr = (reg.absolute_address if not reg.is_array
                     else reg.parent.absolute_address + reg.raw_address_offset)
        csr_addr = byte_addr >> 2
    m = CsrRegModel(
        node=reg,
        name=deref.flat_path(reg, top),
        state_name=deref.reg_state_name(reg, top),
        struct_name=deref.reg_struct_name(reg, top),
        enum_variant=deref.csr_enum_variant(reg, top),
        address=csr_addr,
        regwidth=reg.get_property("regwidth"),
        priv=reg.get_property("riscv_priv"),
        trap_signal=reg.get_property("riscv_trap_signal"),
    )
    for fnode in reg.fields():
        m.fields.append(_scan_field(fnode))
    return m


def _scan_field(f: FieldNode) -> CsrFieldModel:
    warl_raw = f.get_property("riscv_warl")
    warl = parse_warl(warl_raw) if warl_raw else None
    return CsrFieldModel(
        node=f,
        name=deref.field_ident(f),
        msb=f.msb,
        lsb=f.lsb,
        width=f.width,
        sw_readable=f.is_sw_readable,
        sw_writable=f.is_sw_writable,
        hw_readable=f.is_hw_readable,
        hw_writable=f.is_hw_writable,
        reset=int(f.get_property("reset") or 0),
        onwrite=f.get_property("onwrite"),
        onread=f.get_property("onread"),
        wpri=bool(f.get_property("riscv_wpri") or False),
        warl=warl,
        priv=f.get_property("riscv_priv"),
        trap_signal=f.get_property("riscv_trap_signal"),
        save_on_trap=bool(f.get_property("riscv_save_on_trap") or False),
        restore_on_ret=bool(f.get_property("riscv_restore_on_ret") or False),
        hw_mirror=bool(f.get_property("riscv_hw_mirror") or False),
        hw_increment_when=f.get_property("riscv_hw_increment_when") or None,
        hw_increment_high_of=f.get_property("riscv_hw_increment_high_of") or None,
    )


def _camel(snake: str) -> str:
    return "".join(p[:1].upper() + p[1:] for p in snake.split("_") if p)
