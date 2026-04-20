"""Reject unsupported RISC-V CSR constructs with actionable errors."""

from .scan_csrs import CsrDesignModel


class UnsupportedRdlError(Exception):
    pass


def validate(design: CsrDesignModel) -> None:
    for reg in design.regs:
        if reg.regwidth not in (32, 64):
            raise UnsupportedRdlError(
                f"CSR '{reg.node.get_path()}': regwidth must be 32 or 64 "
                f"(got {reg.regwidth})"
            )
        if reg.regwidth > design.xlen:
            raise UnsupportedRdlError(
                f"CSR '{reg.node.get_path()}': regwidth {reg.regwidth} exceeds "
                f"xlen {design.xlen}"
            )
        if reg.node.is_array:
            raise UnsupportedRdlError(
                f"CSR arrays not yet supported (v1): '{reg.node.get_path()}'"
            )
        if reg.priv is not None and reg.priv not in ("m", "s", "u"):
            raise UnsupportedRdlError(
                f"CSR '{reg.node.get_path()}': riscv_priv must be m/s/u "
                f"(got {reg.priv!r})"
            )
        for fld in reg.fields:
            # WPRI and WARL are mutually exclusive — a field is either reserved
            # or has a legalization rule, not both.
            if fld.wpri and fld.warl is not None:
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': riscv_wpri and "
                    f"riscv_warl are mutually exclusive"
                )
            if fld.priv is not None and fld.priv not in ("m", "s", "u"):
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': riscv_priv must be m/s/u "
                    f"(got {fld.priv!r})"
                )
