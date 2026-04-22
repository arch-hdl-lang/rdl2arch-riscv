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
            # save_on_trap writes INTO the field via the CSR file's hwif_in,
            # so the field must be hw_writable (`hw = w` or `hw = rw`).
            if fld.save_on_trap and not fld.hw_writable:
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': riscv_save_on_trap "
                    f"requires `hw = w` or `hw = rw` — the trap coordinator "
                    f"writes the saved value via hwif_in, which only exists "
                    f"for hw-writable fields"
                )
            # hw_mirror drives hwif_in every cycle from an external port —
            # same requirement as save_on_trap: the field has to be
            # hw_writable so the drive takes effect.
            if fld.hw_mirror and not fld.hw_writable:
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': riscv_hw_mirror "
                    f"requires `hw = w` or `hw = rw` — the mirror drive "
                    f"uses the same hwif_in path as save/restore"
                )
            # hw_mirror is the unconditional "track this live wire" mode;
            # save_on_trap / restore_on_ret are event-gated overrides.
            # Mixing them would leave the field's behaviour undefined on
            # non-event cycles (does the mirror win, or hwif_in_live?),
            # so we reject it.
            if fld.hw_mirror and (fld.save_on_trap or fld.restore_on_ret):
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': riscv_hw_mirror is "
                    f"mutually exclusive with riscv_save_on_trap and "
                    f"riscv_restore_on_ret (mirror is an always-on drive, "
                    f"save/restore are event-gated — they can't coexist)"
                )
            # Counter fields self-increment inside the CsrFile's seq
            # block; they must NOT also be driven by hwif_in (that
            # would fight the increment every cycle).
            if (fld.hw_increment_when or fld.hw_increment_high_of) and fld.hw_writable:
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': counter fields "
                    f"(riscv_hw_increment_when / "
                    f"riscv_hw_increment_high_of) must be `hw = r` — the "
                    f"CsrFile's seq block drives them directly; a "
                    f"hwif_in drive would race the increment every cycle"
                )
            # Counter fields don't mix with the trap-lifecycle /
            # mirror family either — the storage either auto-
            # increments OR is event-gated-overridden OR mirrors a
            # live wire. Pick one.
            counter_tag = fld.hw_increment_when or fld.hw_increment_high_of
            if counter_tag and (
                fld.save_on_trap
                or fld.restore_on_ret
                or fld.hw_mirror
            ):
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': counter tags "
                    f"(riscv_hw_increment_*) are mutually exclusive "
                    f"with riscv_save_on_trap / riscv_restore_on_ret / "
                    f"riscv_hw_mirror"
                )
            # Counter fields must be sw-writable so the RISC-V spec's
            # "SW writes replace the counter value" semantic works.
            if counter_tag and not fld.sw_writable:
                raise UnsupportedRdlError(
                    f"field '{fld.node.get_path()}': counter fields "
                    f"(riscv_hw_increment_*) must be `sw = rw` — the "
                    f"spec says SW can overwrite a counter's value"
                )
            # The low-half link has to point at an existing register
            # with exactly one `riscv_hw_increment_when` field, and
            # the widths must match.
            if fld.hw_increment_high_of:
                low_reg_name = fld.hw_increment_high_of
                low_reg = next(
                    (r for r in design.regs if r.name == low_reg_name),
                    None,
                )
                if low_reg is None:
                    raise UnsupportedRdlError(
                        f"field '{fld.node.get_path()}': "
                        f"riscv_hw_increment_high_of = "
                        f"\"{low_reg_name}\" — no such register in this "
                        f"design"
                    )
                low_counter_fields = [
                    lf for lf in low_reg.fields if lf.hw_increment_when
                ]
                if len(low_counter_fields) != 1:
                    raise UnsupportedRdlError(
                        f"field '{fld.node.get_path()}': the register "
                        f"'{low_reg_name}' named in "
                        f"riscv_hw_increment_high_of must contain "
                        f"exactly one field tagged "
                        f"riscv_hw_increment_when (got "
                        f"{len(low_counter_fields)})"
                    )
                if low_counter_fields[0].width != fld.width:
                    raise UnsupportedRdlError(
                        f"field '{fld.node.get_path()}': counter high "
                        f"half width ({fld.width}) must match the low "
                        f"half '{low_reg_name}.{low_counter_fields[0].name}' "
                        f"width ({low_counter_fields[0].width})"
                    )
