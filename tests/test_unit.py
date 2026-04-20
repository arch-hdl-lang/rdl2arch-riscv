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
