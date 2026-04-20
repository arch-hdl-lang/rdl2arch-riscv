"""End-to-end tests through the generated integration top.

The integration wrapper (see `sim/integrated_top.py`) instantiates all
three emitted modules — CSR file, access controller, trap coordinator —
and wires them together so the pybind DUT exposes a single pipeline-
facing interface. These tests exercise cross-module behavior that the
module-isolated Phase 4a tests can't cover:

  - Access controller's `granted` gates CSR file writes/reads
  - Illegal-access CSRs leave CSR state unchanged
  - Trap coordinator's snapshot actually lands in CSR file state on the
    cycle after `trap_enter`
  - Trap-signal pulses still fire through the integrated path
"""

from __future__ import annotations

import pytest

from conftest import RDL_DIR
from sim.driver import reset, tick
from sim.harness import build_sim, fresh_dut


pytest.importorskip("pybind11")


MSTATUS  = 0x300
MTVEC    = 0x305
MEPC     = 0x341
MCAUSE   = 0x342
MSCRATCH = 0x340

PRIV_U = 0b00
PRIV_S = 0b01
PRIV_M = 0b11

OP_NONE  = 0b000
OP_WRITE = 0b001
OP_SET   = 0b010
OP_CLEAR = 0b011


@pytest.fixture(scope="module")
def top_so(arch_bin, tmp_path_factory):
    return build_sim(
        RDL_DIR / "mtrap_subset.rdl",
        target="integrated",
        out_dir=tmp_path_factory.mktemp("integrated"),
        arch_bin=arch_bin,
    )


def _idle(dut) -> None:
    """Default line state between ops: no CSR instruction in flight."""
    dut.csr_cmd_valid = 0
    dut.csr_cmd_addr = 0
    dut.csr_cmd_wdata = 0
    dut.csr_opcode = OP_NONE
    dut.cur_priv = PRIV_M
    dut.trap_enter = 0
    dut.save_mstatus_mpie = 0
    dut.save_mstatus_mpp = 0
    dut.save_mepc_epc = 0
    dut.save_mcause_cause = 0
    dut.save_mtval_tval = 0


def _issue(dut, *, addr: int, opcode: int, wdata: int = 0,
           priv: int = PRIV_M) -> None:
    """Assert a CSR op for one cycle via the cmd handshake, then idle."""
    dut.csr_cmd_addr = addr
    dut.csr_opcode = opcode
    dut.csr_cmd_wdata = wdata
    dut.cur_priv = priv
    dut.csr_cmd_valid = 1
    tick(dut)
    _idle(dut)


def _read(dut, addr: int, *, priv: int = PRIV_M) -> int:
    """Combinational read — rsp_rdata fires same cycle as cmd_valid."""
    dut.csr_cmd_addr = addr
    dut.csr_opcode = OP_NONE
    dut.csr_cmd_wdata = 0
    dut.cur_priv = priv
    dut.csr_cmd_valid = 1
    dut.eval_comb()
    data = dut.csr_rsp_rdata
    _idle(dut)
    dut.eval_comb()
    return data


def test_granted_write_then_read_roundtrips(top_so) -> None:
    """M-priv → M-CSR mscratch write latches and reads back through the
    integrated path."""
    dut = fresh_dut(top_so)
    reset(dut); _idle(dut)
    _issue(dut, addr=MSCRATCH, opcode=OP_WRITE, wdata=0xA5A5_0001, priv=PRIV_M)
    assert _read(dut, MSCRATCH, priv=PRIV_M) == 0xA5A5_0001


def test_priv_fault_leaves_state_unchanged(top_so) -> None:
    """S-priv writing an M-mode CSR must be denied; the CSR keeps its
    old value and `illegal` asserts on the denied cycle."""
    dut = fresh_dut(top_so)
    reset(dut); _idle(dut)
    # Prime with a known value under M-priv.
    _issue(dut, addr=MSCRATCH, opcode=OP_WRITE, wdata=0xDEAD_BEEF, priv=PRIV_M)
    # Attempt a write under S-priv. Observe `illegal` combinationally.
    dut.csr_cmd_addr = MSCRATCH
    dut.csr_opcode = OP_WRITE
    dut.csr_cmd_wdata = 0xBAD_BAD0 & 0xFFFFFFFF
    dut.cur_priv = PRIV_S
    dut.csr_cmd_valid = 1
    dut.eval_comb()
    assert dut.granted == 0
    assert dut.illegal == 1
    tick(dut)
    _idle(dut)
    # Value must be the original.
    assert _read(dut, MSCRATCH, priv=PRIV_M) == 0xDEAD_BEEF


def test_read_only_addr_blocks_write_but_not_read(top_so) -> None:
    """mtrap_subset has no addr[11:10]==11 CSRs, but any M-only write
    at addr 0xCxx would still be blocked. Use a bare read op on the
    (unimplemented) 0xC00 addr — it should be granted as a no-write
    op but read zero from the default mux arm."""
    dut = fresh_dut(top_so)
    reset(dut); _idle(dut)
    val = _read(dut, 0xC00, priv=PRIV_M)
    assert val == 0, f"unimplemented RO addr should mux to 0; got {val:#x}"
    # Attempt a CSRRW into the same RO address — access controller must deny.
    dut.csr_cmd_addr = 0xC00
    dut.csr_opcode = OP_WRITE
    dut.csr_cmd_wdata = 0x1234_5678
    dut.cur_priv = PRIV_M
    dut.csr_cmd_valid = 1
    dut.eval_comb()
    assert dut.granted == 0 and dut.illegal == 1


def test_trap_signal_pulses_through_integrated_path(top_so) -> None:
    """Writing mtvec must pulse mtvec_write at the top level."""
    dut = fresh_dut(top_so)
    reset(dut); _idle(dut)
    assert dut.mtvec_write == 0
    _issue(dut, addr=MTVEC, opcode=OP_WRITE, wdata=0x4, priv=PRIV_M)
    # Pulse is registered in the CSR file; after the write tick it's high
    # for one cycle, then falls.
    dut.eval_comb()
    # The issue tick raised the pulse reg; it stays high until next tick.
    # We already ticked once inside _issue, which is what exposed the pulse.
    # Confirm it's readable in the post-write idle cycle, then falls.
    assert dut.mtvec_write == 1
    tick(dut); _idle(dut); tick(dut)
    assert dut.mtvec_write == 0


def test_trap_enter_snapshots_mepc_through_wrapper(top_so) -> None:
    """`trap_enter` must route `save_mepc_epc` through the trap
    coordinator into the CSR file and be visible on the next read."""
    dut = fresh_dut(top_so)
    reset(dut); _idle(dut)
    # Confirm mepc starts at 0.
    assert _read(dut, MEPC, priv=PRIV_M) == 0
    # Assert trap_enter with the save port holding the faulting PC.
    dut.save_mepc_epc = 0x8000_1000
    dut.trap_enter = 1
    tick(dut)
    _idle(dut)
    # mepc has WARL mask 0xFFFFFFFE (bit 0 forced to 0). 0x8000_1000 is even,
    # so it survives unchanged.
    assert _read(dut, MEPC, priv=PRIV_M) == 0x8000_1000


def test_trap_enter_does_not_fire_csr_write(top_so) -> None:
    """A trap-entry cycle with no CSR op in flight must not corrupt
    unrelated CSRs (e.g. mscratch)."""
    dut = fresh_dut(top_so)
    reset(dut); _idle(dut)
    _issue(dut, addr=MSCRATCH, opcode=OP_WRITE, wdata=0xCAFE_F00D, priv=PRIV_M)
    # Trap entry — no CSR op asserted.
    dut.save_mepc_epc = 0x1000
    dut.trap_enter = 1
    dut.csr_cmd_valid = 0
    tick(dut); _idle(dut)
    # mscratch unchanged, mepc took the snapshot.
    assert _read(dut, MSCRATCH, priv=PRIV_M) == 0xCAFE_F00D
    assert _read(dut, MEPC, priv=PRIV_M) == 0x1000
