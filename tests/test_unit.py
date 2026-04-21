"""Unit tests for UDP registration + scan/validate."""

import pytest
from systemrdl import RDLCompiler

from rdl2arch_riscv.scan_csrs import scan
from rdl2arch_riscv.udps import ALL_UDPS
from rdl2arch_riscv.udps.warl import parse_warl
from rdl2arch_riscv.validate_csrs import UnsupportedRdlError, validate


def _compile_rdl(tmp_path, source: str):
    rdl = tmp_path / "x.rdl"
    rdl.write_text(source)
    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        rdlc.register_udp(udp, soft=False)
    rdlc.compile_file(str(rdl))
    return rdlc.elaborate().top


def test_riscv_csr_addr_udp_overrides_rdl_byte_addr(tmp_path) -> None:
    """`riscv_csr_addr` wins over RDL `@` — scanner reads the UDP."""
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                riscv_csr_addr = 0x300;
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } mstatus @ 0x0;       // RDL byte address ignored
            reg {
                riscv_csr_addr = 0x305;
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } mtvec @ 0x4;
        };
    """)
    d = scan(top)
    assert [r.address for r in d.regs] == [0x300, 0x305]


def test_legacy_byte_address_fallback(tmp_path) -> None:
    """Without `riscv_csr_addr`, scanner falls back to `byte_addr >> 2`."""
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } r0 @ 0xC00;           // CSR 0x300 under old convention
        };
    """)
    d = scan(top)
    assert d.regs[0].address == 0x300


def test_default_priv_at_addrmap(tmp_path) -> None:
    """`default riscv_priv = "m";` at addrmap propagates to descendant regs."""
    top = _compile_rdl(tmp_path, """
        addrmap t {
            default riscv_priv = "m";
            reg {
                riscv_csr_addr = 0x300;
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } mstatus @ 0x0;
        };
    """)
    d = scan(top)
    assert d.regs[0].priv == "m"


def test_parse_warl_mask() -> None:
    assert parse_warl("0x1F") == ("mask", 0x1F)
    assert parse_warl("0b101") == ("mask", 0b101)
    assert parse_warl("42") == ("mask", 42)


def test_parse_warl_enum() -> None:
    kind, legal = parse_warl("0,1,3")
    assert kind == "enum"
    assert legal == [0, 1, 3]


def test_scan_captures_udps(tmp_path) -> None:
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                field { sw = rw; hw = r; reset = 0; riscv_wpri = true; } reserved[0:0];
                field { sw = rw; hw = r; reset = 0;
                        riscv_warl = "0x3"; riscv_save_on_trap = true; } mpp[2:1];
                field { sw = rw; hw = rw; reset = 0;
                        riscv_trap_signal = "my_pulse"; } mie[3:3];
            } mstatus @ 0xC00;
        };
    """)
    d = scan(top)
    assert d.xlen == 32
    assert len(d.regs) == 1
    reg = d.regs[0]
    assert reg.address == 0x300    # 0xC00 >> 2
    reserved, mpp, mie = reg.fields
    assert reserved.wpri is True
    assert mpp.warl == ("mask", 0x3)
    assert mpp.save_on_trap is True
    assert mie.trap_signal == "my_pulse"


def test_access_controller_emits_priv_override(tmp_path) -> None:
    from rdl2arch_riscv.emit_access_controller import emit_access_controller
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } no_override @ 0xC00;    // M-priv via addr bits
            reg {
                riscv_priv = "s";
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } override_s @ 0xC04;      // CSR 0x301, explicit S-priv override
        };
    """)
    d = scan(top)
    src = emit_access_controller(d, "TCsrAccess")
    assert "12'h301 => 2'b01," in src, f"missing S-priv override in:\n{src}"
    assert "12'h300" not in src, f"non-overridden CSR leaked into match:\n{src}"
    assert "_       => csr_addr[9:8]" in src


def test_access_controller_no_overrides(tmp_path) -> None:
    from rdl2arch_riscv.emit_access_controller import emit_access_controller
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } r0 @ 0xC00;
        };
    """)
    d = scan(top)
    src = emit_access_controller(d, "TCsrAccess")
    assert "let min_priv: UInt<2> = csr_addr[9:8];" in src
    assert "match csr_addr" not in src


def test_trap_coord_save_on_trap_port_and_mux(tmp_path) -> None:
    from rdl2arch_riscv.emit_trap_coordinator import emit_trap_coordinator
    top = _compile_rdl(tmp_path, """
        addrmap t {
            default riscv_priv = "m";
            reg {
                riscv_csr_addr = 0x341;
                field { sw = rw; hw = rw; reset = 0;
                        riscv_save_on_trap = true; } epc[31:0];
            } mepc @ 0x0;
            reg {
                riscv_csr_addr = 0x340;
                field { sw = rw; hw = r; reset = 0; } value[31:0];
            } mscratch @ 0x4;
        };
    """)
    d = scan(top)
    src = emit_trap_coordinator(d, "TCsrTrapCoord")
    # save_on_trap field → save_<member> port declared
    assert "port save_mepc_epc: in UInt<32>;" in src
    # save_on_trap field gets the trap_enter mux
    assert "trap_enter ? save_mepc_epc : hwif_in_live.mepc_epc" in src
    # mscratch.value is hw=r (not writable) → not in hwif_in at all
    assert "mscratch_value" not in src


def test_trap_coord_no_save_fields_still_compiles(tmp_path) -> None:
    from rdl2arch_riscv.emit_trap_coordinator import emit_trap_coordinator
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                riscv_csr_addr = 0x340;
                field { sw = rw; hw = r; reset = 0; } v[31:0];
            } mscratch @ 0x0;
        };
    """)
    d = scan(top)
    src = emit_trap_coordinator(d, "TCsrTrapCoord")
    # No save_on_trap fields, no save_ ports, no trap_enter mux.
    assert "save_" not in src
    assert "trap_enter ?" not in src
    # xret_enter pulse is still declared (symmetric with trap_enter);
    # it's unused when no fields carry `riscv_restore_on_ret`.
    assert "port xret_enter: in Bool;" in src
    assert "restore_" not in src
    # Still legal ARCH: the HwifIn has only `_reserved` in this case.
    assert "hwif_in_drive._reserved" in src


def test_trap_coord_hw_mirror_port_and_drive(tmp_path) -> None:
    """`riscv_hw_mirror = true` on a field makes the generator emit a
    dedicated `mirror_<reg>_<field>` input port and drive
    `hwif_in_drive.<member>` from it unconditionally — bypassing
    `hwif_in_live` and any save/restore gating."""
    from rdl2arch_riscv.emit_trap_coordinator import emit_trap_coordinator
    top = _compile_rdl(tmp_path, """
        addrmap t {
            default riscv_priv = "m";
            reg {
                riscv_csr_addr = 0x344;
                field { sw = r; hw = w; reset = 0;
                        riscv_wpri = true; } wpri_2_0[2:0];
                field { sw = r; hw = w; reset = 0;
                        riscv_hw_mirror = true; } msip[3:3];
                field { sw = r; hw = w; reset = 0;
                        riscv_wpri = true; } wpri_hi[31:4];
            } mip @ 0x0;
        };
    """)
    d = scan(top)
    src = emit_trap_coordinator(d, "TCsrTrapCoord")

    # mirror_ port declared.
    assert "port mirror_mip_msip: in UInt<1>;" in src
    # Mirror field gets the unconditional drive — no trap/xret gating.
    assert "hwif_in_drive.mip_msip = mirror_mip_msip;" in src
    # WPRI fields still pass through hwif_in_live.
    assert (
        "hwif_in_drive.mip_wpri_2_0 = hwif_in_live.mip_wpri_2_0"
    ) in src


def test_validate_rejects_hw_mirror_without_hw_writable(tmp_path) -> None:
    from rdl2arch_riscv.validate_csrs import validate, UnsupportedRdlError
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                riscv_csr_addr = 0x344;
                field { sw = r; hw = r; reset = 0;
                        riscv_hw_mirror = true; } msip[3:3];
            } mip @ 0x0;
        };
    """)
    d = scan(top)
    try:
        validate(d)
    except UnsupportedRdlError as e:
        assert "riscv_hw_mirror" in str(e)
        assert "hw_writable" in str(e) or "hw = w" in str(e)
    else:
        raise AssertionError("expected UnsupportedRdlError for hw_mirror on sw=r;hw=r field")


def test_validate_rejects_hw_mirror_with_save_on_trap(tmp_path) -> None:
    from rdl2arch_riscv.validate_csrs import validate, UnsupportedRdlError
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                riscv_csr_addr = 0x344;
                field { sw = rw; hw = rw; reset = 0;
                        riscv_hw_mirror = true;
                        riscv_save_on_trap = true; } bogus[3:3];
            } mip @ 0x0;
        };
    """)
    d = scan(top)
    try:
        validate(d)
    except UnsupportedRdlError as e:
        assert "riscv_hw_mirror" in str(e)
        assert "save_on_trap" in str(e) or "restore_on_ret" in str(e)
    else:
        raise AssertionError("expected UnsupportedRdlError for hw_mirror + save_on_trap")


def test_csr_file_emits_reg_rdata_flat(tmp_path) -> None:
    """Every register gains a `<reg>_rdata_flat: UInt<xlen>` member on
    hwif_out, driven with the same spec-layout expression the SW
    readback mux uses. Lets adapters consume register values without
    depending on packed-struct field-naming conventions."""
    from rdl2arch_riscv.emit_csr_package import emit_package
    from rdl2arch_riscv.emit_csr_file import emit_csr_file
    top = _compile_rdl(tmp_path, """
        addrmap t {
            default riscv_priv = "m";
            reg {
                riscv_csr_addr = 0x304;
                field { sw = r;  hw = w; reset = 0;
                        riscv_wpri = true; } wpri_2_0[2:0];
                field { sw = rw; hw = r; reset = 0; } msie[3:3];
                field { sw = r;  hw = w; reset = 0;
                        riscv_wpri = true; } wpri_hi[31:4];
            } mie @ 0x0;
        };
    """)
    d = scan(top)
    pkg = emit_package(d)
    csrfile = emit_csr_file(d)
    # hwif_out struct declares the flat member.
    assert "mie_rdata_flat: UInt<32>;" in pkg
    # CSR file comb-drives it — the spec-layout value is a concat
    # that includes the `mie.msie` field at bit 3 (our scan-time
    # expression pads above and below).
    assert "hwif_out.mie_rdata_flat =" in csrfile
    # Same expression the SW readback mux uses — containing at
    # least the storage reference for msie.
    assert "mie_r.msie" in csrfile


def test_trap_coord_restore_on_ret_port_and_mux(tmp_path) -> None:
    """`riscv_restore_on_ret` on a field makes the generator emit a
    `restore_<reg>_<field>` input port and an `xret_enter ? restore_<m>
    : …` mux on that member."""
    from rdl2arch_riscv.emit_trap_coordinator import emit_trap_coordinator
    top = _compile_rdl(tmp_path, """
        addrmap t {
            default riscv_priv = "m";
            reg {
                riscv_csr_addr = 0x300;
                // mie: restore-only (no save_on_trap) — mstatus.mie
                // pattern. Gets written from a port on xret_enter;
                // hwif_in_live carries the non-xret value (including
                // the trap-entry auto-clear).
                field { sw = rw; hw = rw; reset = 0;
                        riscv_restore_on_ret = true; } mie[3:3];
                field { sw = r; hw = w; reset = 0;
                        riscv_wpri = true; } reserved_6_4[6:4];
                // mpie: save AND restore — save on trap, restore on
                // xret. Generator must emit both save_ and restore_
                // ports AND a 3-way `trap_enter ? save : xret_enter
                // ? restore : live` mux with trap having priority.
                field { sw = rw; hw = rw; reset = 0;
                        riscv_save_on_trap = true;
                        riscv_restore_on_ret = true; } mpie[7:7];
                field { sw = r; hw = w; reset = 0;
                        riscv_wpri = true; } wpri_hi[31:8];
            } mstatus @ 0x0;
        };
    """)
    d = scan(top)
    src = emit_trap_coordinator(d, "TCsrTrapCoord")

    # Both pulse ports are declared (order: trap first, xret second).
    assert "port trap_enter: in Bool;" in src
    assert "port xret_enter: in Bool;" in src

    # restore-only field → only xret mux, no save port for it.
    assert "port restore_mstatus_mie: in UInt<1>;" in src
    assert "save_mstatus_mie" not in src
    assert (
        "hwif_in_drive.mstatus_mie = "
        "xret_enter ? restore_mstatus_mie : hwif_in_live.mstatus_mie"
    ) in src

    # save+restore field → 3-way priority mux, trap_enter wins over xret.
    assert "port save_mstatus_mpie: in UInt<1>;" in src
    assert "port restore_mstatus_mpie: in UInt<1>;" in src
    assert (
        "hwif_in_drive.mstatus_mpie = "
        "trap_enter ? save_mstatus_mpie : "
        "xret_enter ? restore_mstatus_mpie : "
        "hwif_in_live.mstatus_mpie"
    ) in src

    # Untagged WPRI stays pure pass-through — no trap_enter / xret_enter.
    assert (
        "hwif_in_drive.mstatus_reserved_6_4 = hwif_in_live.mstatus_reserved_6_4"
    ) in src
    assert (
        "hwif_in_drive.mstatus_wpri_hi = hwif_in_live.mstatus_wpri_hi"
    ) in src


def test_validate_rejects_save_on_trap_without_hw_writable(tmp_path) -> None:
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                riscv_csr_addr = 0x341;
                field { sw = rw; hw = r; reset = 0;
                        riscv_save_on_trap = true; } epc[31:0];
            } mepc @ 0x0;
        };
    """)
    d = scan(top)
    with pytest.raises(UnsupportedRdlError, match="riscv_save_on_trap"):
        validate(d)


def test_validate_rejects_wpri_and_warl_together(tmp_path) -> None:
    top = _compile_rdl(tmp_path, """
        addrmap t {
            reg {
                field { sw = rw; hw = r; reset = 0;
                        riscv_wpri = true; riscv_warl = "0x3"; } bad[1:0];
            } r0 @ 0x0;
        };
    """)
    d = scan(top)
    with pytest.raises(UnsupportedRdlError, match="mutually exclusive"):
        validate(d)


# ── CLINT UDP + scan/emit ─────────────────────────────────────────────────


def test_clint_scan_buckets_regs_by_role(tmp_path) -> None:
    from rdl2arch_riscv.emit_clint_logic import scan_clint
    top = _compile_rdl(tmp_path, """
        addrmap c {
            reg {
                riscv_intr_clint_role = "msip";
                field { sw = rw; hw = r; reset = 0; } value[0:0];
                field { sw = r;  hw = r; reset = 0; } reserved[31:1];
            } msip @ 0x0000;
            reg {
                riscv_intr_clint_role = "mtimecmp_lo";
                field { sw = rw; hw = r; reset = 0xFFFFFFFF; } v[31:0];
            } mtimecmp_lo @ 0x4000;
            reg {
                riscv_intr_clint_role = "mtimecmp_hi";
                field { sw = rw; hw = r; reset = 0xFFFFFFFF; } v[31:0];
            } mtimecmp_hi @ 0x4004;
            reg {
                riscv_intr_clint_role = "mtime_lo";
                field { sw = rw; hw = rw; reset = 0; } v[31:0];
            } mtime_lo @ 0xBFF8;
            reg {
                riscv_intr_clint_role = "mtime_hi";
                field { sw = rw; hw = rw; reset = 0; } v[31:0];
            } mtime_hi @ 0xBFFC;
        };
    """)
    m = scan_clint(top, module_name="C", package_name="CPkg")
    assert m.msip is not None and m.msip.inst_name == "msip"
    assert m.mtimecmp_lo is not None and m.mtimecmp_hi is not None
    assert m.mtime_lo is not None and m.mtime_hi is not None


def test_clint_emit_logic_has_expected_ports(tmp_path) -> None:
    from rdl2arch_riscv.emit_clint_logic import scan_clint, emit_clint_logic
    top = _compile_rdl(tmp_path, """
        addrmap c {
            reg { riscv_intr_clint_role = "msip";
                  field { sw = rw; hw = r; reset = 0; } value[0:0];
                  field { sw = r;  hw = r; reset = 0; } reserved[31:1];
                } msip @ 0x0000;
            reg { riscv_intr_clint_role = "mtimecmp_lo";
                  field { sw = rw; hw = r; reset = 0xFFFFFFFF; } v[31:0];
                } mtimecmp_lo @ 0x4000;
            reg { riscv_intr_clint_role = "mtimecmp_hi";
                  field { sw = rw; hw = r; reset = 0xFFFFFFFF; } v[31:0];
                } mtimecmp_hi @ 0x4004;
            reg { riscv_intr_clint_role = "mtime_lo";
                  field { sw = rw; hw = rw; reset = 0; } v[31:0];
                } mtime_lo @ 0xBFF8;
            reg { riscv_intr_clint_role = "mtime_hi";
                  field { sw = rw; hw = rw; reset = 0; } v[31:0];
                } mtime_hi @ 0xBFFC;
        };
    """)
    m = scan_clint(top, module_name="C", package_name="CPkg")
    src = emit_clint_logic(m, "CLogic")
    # Ports
    for tok in ("port clk:", "port rst:", "port mtime_tick:",
                "port hwif_out:", "port hwif_in:",
                "port msip_out:", "port mtip_out:"):
        assert tok in src, f"missing `{tok}` in:\n{src}"
    # 64-bit concat + comparator
    assert "{hwif_out.mtime_hi_v, hwif_out.mtime_lo_v}" in src
    assert "mtip_out = mtime >= mtimecmp;" in src
    # msip as passthrough of bit 0
    assert "msip_out = hwif_out.msip_value != 1'h0;" in src


def test_clint_emit_rejects_missing_regs(tmp_path) -> None:
    import pytest
    from rdl2arch_riscv.emit_clint_logic import scan_clint, emit_clint_logic
    top = _compile_rdl(tmp_path, """
        addrmap c {
            reg { riscv_intr_clint_role = "msip";
                  field { sw = rw; hw = r; reset = 0; } value[0:0];
                  field { sw = r;  hw = r; reset = 0; } reserved[31:1];
                } msip @ 0x0;
            // missing mtime* / mtimecmp* entries
        };
    """)
    m = scan_clint(top, module_name="C", package_name="CPkg")
    with pytest.raises(ValueError, match="mtimecmp"):
        emit_clint_logic(m, "CLogic")


# ── PLIC UDP + scan/emit ──────────────────────────────────────────────────

_PLIC_SRC = """
    addrmap p {
        reg { riscv_intr_plic_role = "priority";
              field { sw = rw; hw = r; reset = 0; } value[2:0];
              field { sw = r;  hw = r; reset = 0; } reserved[31:3];
            } priority[5] @ 0x0000;
        reg { riscv_intr_plic_role = "pending";
              field { sw = r; hw = w; reset = 0; } value[4:0];
              field { sw = r; hw = r; reset = 0; } reserved[31:5];
            } pending @ 0x1000;
        reg { riscv_intr_plic_role = "enable";
              field { sw = rw; hw = r; reset = 0; } value[4:0];
              field { sw = r;  hw = r; reset = 0; } reserved[31:5];
            } enable_0 @ 0x2000;
        reg { riscv_intr_plic_role = "threshold";
              field { sw = rw; hw = r; reset = 0; } value[2:0];
              field { sw = r;  hw = r; reset = 0; } reserved[31:3];
            } threshold_0 @ 0x200000;
        reg { riscv_intr_plic_role = "claim";
              emit_read_pulse  = true;
              emit_write_pulse = true;
              field { sw = rw; hw = rw; reset = 0; } value[3:0];
              field { sw = r;  hw = r;  reset = 0; } reserved[31:4];
            } claim_0 @ 0x200004;
    };
"""


def test_plic_scan_buckets_regs_by_role(tmp_path) -> None:
    from rdl2arch_riscv.emit_plic_logic import scan_plic
    top = _compile_rdl(tmp_path, _PLIC_SRC)
    m = scan_plic(top, module_name="P", package_name="PPkg")
    assert m.pending is not None
    # Single-context fixture → one enable/threshold/claim each.
    assert len(m.enables) == 1
    assert len(m.thresholds) == 1
    assert len(m.claims) == 1
    assert m.n_contexts == 1
    # priority[5] gives us 5 priority regs; n_sources = 4 (sources 1..4;
    # source 0 is reserved).
    assert len(m.priorities) == 5
    assert m.n_sources == 4


def test_plic_emit_has_arbiter_structure(tmp_path) -> None:
    from rdl2arch_riscv.emit_plic_logic import scan_plic, emit_plic_logic
    top = _compile_rdl(tmp_path, _PLIC_SRC)
    m = scan_plic(top, module_name="P", package_name="PPkg")
    src = emit_plic_logic(m, "PLogic")
    # Per-source candidate flags for sources 1..4 in context 0. Each
    # must AND in the !claimed-bit mask so a claimed source can't win
    # again until the matching complete fires.
    for i in range(1, 5):
        assert f"let c0_cand_{i}: Bool" in src, f"missing c0_cand_{i}:\n{src}"
        assert f"hwif_out.priority_{i}_value" in src, (
            f"missing priority_{i} indexed ref:\n{src}"
        )
        assert f"c0_claimed_r[{i}] == 1'b0" in src, (
            f"candidate {i} missing !claimed gating:\n{src}"
        )
    # Cascade chain for context 0.
    assert "let c0_w1_id:" in src
    assert "let c0_w4_id:" in src
    # Claim/complete scaffolding: pulse ports, in-service reg, delayed pulse,
    # set/clear masks folded into one seq update.
    assert "port claim_0_read_pulse:  in Bool;" in src
    assert "port claim_0_write_pulse: in Bool;" in src
    assert "reg c0_claimed_r: UInt<5>" in src
    assert "reg c0_wr_pulse_d: Bool" in src
    assert "c0_set_mask: UInt<5> = claim_0_read_pulse ? c0_set_bit" in src
    assert "c0_clr_mask: UInt<5> = c0_wr_pulse_d ? c0_clr_bit" in src
    assert "c0_wr_pulse_d <= claim_0_write_pulse;" in src
    assert (
        "c0_claimed_r <= (c0_claimed_r | c0_set_mask) & (~c0_clr_mask);" in src
    )
    # Outputs.
    assert "hwif_in.pending_value = source_in;" in src
    assert "hwif_in.claim_0_value = c0_w4_id;" in src
    # 4 sources → 3-bit ID; single-context fixture → scalar bool-to-UInt<1>.
    assert "intr_out = c0_w4_id != 3'h0;" in src


def test_plic_emit_rejects_missing_regs(tmp_path) -> None:
    import pytest
    from rdl2arch_riscv.emit_plic_logic import scan_plic, emit_plic_logic
    top = _compile_rdl(tmp_path, """
        addrmap p {
            reg { riscv_intr_plic_role = "priority";
                  field { sw = rw; hw = r; reset = 0; } value[2:0];
                  field { sw = r;  hw = r; reset = 0; } reserved[31:3];
                } priority[3] @ 0x0;
            // missing pending / enable / threshold / claim
        };
    """)
    m = scan_plic(top, module_name="P", package_name="PPkg")
    with pytest.raises(ValueError, match="pending"):
        emit_plic_logic(m, "PLogic")


def test_plic_emit_rejects_claim_without_pulses(tmp_path) -> None:
    """The generator now hard-requires `emit_read_pulse` and
    `emit_write_pulse` on each claim reg — the emitted logic relies on
    the pulses to latch / clear the in-service state. A plain read-only
    claim (the old Phase 5.2 shape) is rejected with a pointer at the
    UDP the user needs to add."""
    import pytest
    from rdl2arch_riscv.emit_plic_logic import scan_plic, emit_plic_logic
    top = _compile_rdl(tmp_path, """
        addrmap p {
            reg { riscv_intr_plic_role = "priority";
                  field { sw = rw; hw = r; reset = 0; } value[2:0];
                  field { sw = r;  hw = r; reset = 0; } reserved[31:3];
                } priority[3] @ 0x0;
            reg { riscv_intr_plic_role = "pending";
                  field { sw = r; hw = w; reset = 0; } value[2:0];
                  field { sw = r; hw = r; reset = 0; } reserved[31:3];
                } pending @ 0x1000;
            reg { riscv_intr_plic_role = "enable";
                  field { sw = rw; hw = r; reset = 0; } value[2:0];
                  field { sw = r;  hw = r; reset = 0; } reserved[31:3];
                } enable_0 @ 0x2000;
            reg { riscv_intr_plic_role = "threshold";
                  field { sw = rw; hw = r; reset = 0; } value[2:0];
                  field { sw = r;  hw = r; reset = 0; } reserved[31:3];
                } threshold_0 @ 0x200000;
            // Missing both pulses.
            reg { riscv_intr_plic_role = "claim";
                  field { sw = r; hw = w; reset = 0; } value[2:0];
                  field { sw = r; hw = r; reset = 0; } reserved[31:3];
                } claim_0 @ 0x200004;
        };
    """)
    m = scan_plic(top, module_name="P", package_name="PPkg")
    with pytest.raises(ValueError, match="emit_read_pulse"):
        emit_plic_logic(m, "PLogic")
