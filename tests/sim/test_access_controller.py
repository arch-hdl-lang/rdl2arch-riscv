"""Functional sim tests for the emitted access controller.

Pure combinational module: drives `csr_addr`, `csr_opcode`, `cur_priv`,
`valid`; checks `granted`, `illegal`, `cause`. Exercises the three
privilege levels, the read-only check (addr[11:10] == 2'b11), and
per-register priv overrides.
"""

from __future__ import annotations

import pytest

from sim.harness import fresh_dut


pytest.importorskip("pybind11")


PRIV_U = 0b00
PRIV_S = 0b01
PRIV_M = 0b11

OP_NONE  = 0b00  # bit [1:0] == 00 — controller treats as "no write"
OP_WRITE = 0b01
OP_SET   = 0b10
OP_CLEAR = 0b11


def _access(dut, *, addr: int, opcode: int, priv: int, valid: bool = True) -> tuple:
    dut.csr_addr = addr
    dut.csr_opcode = opcode
    dut.cur_priv = priv
    dut.valid = 1 if valid else 0
    dut.eval_comb()
    return dut.granted, dut.illegal, dut.cause


@pytest.fixture(scope="module")
def mtrap_access(mtrap_sim_build) -> str:
    # mtrap_subset has no per-reg priv overrides — only the default
    # `riscv_priv = "m"` at the addrmap level, which doesn't trigger an
    # override arm. Exercises the csr_addr[9:8] default path.
    return mtrap_sim_build["access"]


@pytest.fixture(scope="module")
def override_access(override_sim_build) -> str:
    return override_sim_build["access"]


# ── csr_addr[9:8] default priv path (mtrap_subset, no overrides) ────────────


def test_m_mode_csr_allows_m(mtrap_access) -> None:
    """mstatus @ 0x300 — addr[9:8]=11 → M-priv. Current M allows."""
    dut = fresh_dut(mtrap_access)
    granted, illegal, cause = _access(dut, addr=0x300, opcode=OP_WRITE, priv=PRIV_M)
    assert granted == 1 and illegal == 0


def test_m_mode_csr_denies_s(mtrap_access) -> None:
    """S-priv trying to access an M-priv CSR must be denied + illegal."""
    dut = fresh_dut(mtrap_access)
    granted, illegal, cause = _access(dut, addr=0x300, opcode=OP_WRITE, priv=PRIV_S)
    assert granted == 0 and illegal == 1
    assert cause == 2, "illegal-instruction cause should be 2"


def test_m_mode_csr_denies_u(mtrap_access) -> None:
    dut = fresh_dut(mtrap_access)
    granted, illegal, _ = _access(dut, addr=0x300, opcode=OP_WRITE, priv=PRIV_U)
    assert granted == 0 and illegal == 1


def test_read_only_csr_addr_rejects_write(mtrap_access) -> None:
    """addr[11:10]==11 marks a read-only CSR. Any write opcode must fail
    even when priv matches."""
    # 0xC00 is read-only, M-priv. A write from M-priv should still be denied.
    dut = fresh_dut(mtrap_access)
    granted, illegal, _ = _access(dut, addr=0xC00, opcode=OP_WRITE, priv=PRIV_M)
    assert granted == 0 and illegal == 1


def test_read_only_csr_addr_allows_read(mtrap_access) -> None:
    """OP_NONE (opcode[1:0]==00) == no write — must be granted on the
    read-only address even though a write wouldn't be."""
    dut = fresh_dut(mtrap_access)
    granted, illegal, _ = _access(dut, addr=0xC00, opcode=OP_NONE, priv=PRIV_M)
    assert granted == 1 and illegal == 0


def test_valid_low_inhibits_both(mtrap_access) -> None:
    """With `valid` deasserted neither `granted` nor `illegal` fire."""
    dut = fresh_dut(mtrap_access)
    granted, illegal, _ = _access(dut, addr=0x300, opcode=OP_WRITE,
                                  priv=PRIV_M, valid=False)
    assert granted == 0 and illegal == 0


# ── per-register priv override (priv_override fixture) ──────────────────────


def test_override_forces_s_priv(override_access) -> None:
    """debug_peek @ 0x301 has `riscv_priv = "s"`. M access → granted;
    U access → denied."""
    dut = fresh_dut(override_access)
    granted_m, _, _ = _access(dut, addr=0x301, opcode=OP_SET, priv=PRIV_M)
    granted_s, _, _ = _access(dut, addr=0x301, opcode=OP_SET, priv=PRIV_S)
    granted_u, illegal_u, _ = _access(dut, addr=0x301, opcode=OP_SET, priv=PRIV_U)
    assert granted_m == 1
    assert granted_s == 1, "S-priv must satisfy S-priv override"
    assert granted_u == 0 and illegal_u == 1


def test_override_does_not_apply_to_non_override_addr(override_access) -> None:
    """The non-overridden mstatus @ 0x300 still uses addr[9:8]=11 → M."""
    dut = fresh_dut(override_access)
    granted_m, _, _ = _access(dut, addr=0x300, opcode=OP_SET, priv=PRIV_M)
    granted_s, illegal_s, _ = _access(dut, addr=0x300, opcode=OP_SET, priv=PRIV_S)
    assert granted_m == 1
    assert granted_s == 0 and illegal_s == 1
