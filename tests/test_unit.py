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
    # Still legal ARCH: the HwifIn has only `_reserved` in this case.
    assert "hwif_in_drive._reserved" in src


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
