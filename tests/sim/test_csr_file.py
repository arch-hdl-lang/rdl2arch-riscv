"""Functional sim tests for the emitted CSR file.

Builds a pybind11-wrapped sim model from `mtrap_subset.rdl` and exercises
it through the pipeline interface. Verifies the semantic behaviors the
emitter is responsible for: WPRI readback masking, WARL mask/enum
coercion on writes, trap-signal pulses, and hwif_in pass-through to
internal state.
"""

from __future__ import annotations

import pytest

from conftest import RDL_DIR
from sim.driver import CsrPipelineDriver, reset, tick
from sim.harness import build_sim, fresh_dut


pytest.importorskip("pybind11")


MSTATUS  = 0x300
MTVEC    = 0x305
MEPC     = 0x341
MCAUSE   = 0x342
MSCRATCH = 0x340


@pytest.fixture(scope="module")
def csr_so(arch_bin, tmp_path_factory):
    return build_sim(
        RDL_DIR / "mtrap_subset.rdl",
        target="csr_file",
        out_dir=tmp_path_factory.mktemp("csr_file"),
        arch_bin=arch_bin,
    )


def test_reset_state_reads_zero(csr_so) -> None:
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    for addr in (MSTATUS, MTVEC, MEPC, MCAUSE, MSCRATCH):
        assert drv.read(addr) == 0, f"CSR {addr:#x} should read 0 after reset"


def test_mscratch_roundtrip(csr_so) -> None:
    """mscratch is plain sw=rw + hw=r. Write arbitrary value, read back."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    drv.write(MSCRATCH, 0xDEADBEEF)
    assert drv.read(MSCRATCH) == 0xDEADBEEF


def test_mstatus_wpri_readback_masks_reserved_bits(csr_so) -> None:
    """Bits tagged `riscv_wpri` read as zero regardless of state."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    # Write all-ones. MIE[3], MPIE[7], MPP[12:11] are the only readable slots.
    drv.write(MSTATUS, 0xFFFFFFFF)
    rd = drv.read(MSTATUS)
    mie_ok  = (rd >> 3) & 1
    mpie_ok = (rd >> 7) & 1
    mpp_ok  = (rd >> 11) & 0x3
    assert mie_ok == 1, f"MIE did not latch: rd={rd:#x}"
    assert mpie_ok == 1, f"MPIE did not latch: rd={rd:#x}"
    assert mpp_ok == 0x3, f"MPP did not latch: rd={rd:#x}"
    # All other bits must read zero (WPRI in this layout).
    expected_mask = (1 << 3) | (1 << 7) | (0x3 << 11)
    assert rd & ~expected_mask == 0, \
        f"WPRI bits leaked: rd={rd:#x}, expected only {expected_mask:#x} set"


def test_mepc_warl_mask_clears_bit_zero(csr_so) -> None:
    """mepc has `riscv_warl = "0xFFFFFFFE"` — writes with bit 0 set are
    coerced to even."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    drv.write(MEPC, 0x1001)
    assert drv.read(MEPC) == 0x1000, "bit 0 should be forced to 0 by WARL mask"
    drv.write(MEPC, 0xFFFFFFFF)
    assert drv.read(MEPC) == 0xFFFFFFFE


def test_mtvec_warl_enum_coerces_illegal_mode(csr_so) -> None:
    """mtvec.mode has `riscv_warl = "0,1"` — writing 2 or 3 is coerced to 0."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    # Write mode=1, base=0x1000_0000 → should round-trip.
    drv.write(MTVEC, (0x1000_0000 << 2) | 1)
    rd = drv.read(MTVEC)
    assert rd & 0x3 == 1, f"mode=1 should stick: rd={rd:#x}"
    # Write mode=2 (illegal) → coerced to 0 (smallest legal).
    drv.write(MTVEC, (0x2000_0000 << 2) | 2)
    rd = drv.read(MTVEC)
    assert rd & 0x3 == 0, f"illegal mode=2 should coerce to 0: rd={rd:#x}"
    # base[31:2] stays as-written.
    assert (rd >> 2) & 0x3FFFFFFF == 0x2000_0000


def test_mtvec_trap_signal_pulses_once_on_write(csr_so) -> None:
    """`riscv_trap_signal = "mtvec_write"` on mtvec.mode should emit a
    one-cycle-high pulse on any write to mtvec, then go low."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    assert dut.mtvec_write == 0, "pulse should be low after reset"
    drv.write(MTVEC, 0x4)
    assert dut.mtvec_write == 1, "pulse should be high on the write cycle"
    tick(dut)
    assert dut.mtvec_write == 0, "pulse must fall back to 0 after one cycle"


def test_csrrs_sets_bits(csr_so) -> None:
    """CSRRS opcode (10): new = old | wdata."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    drv.write(MSCRATCH, 0x0000_FF00)
    drv.set(MSCRATCH, 0x00FF_0000)
    assert drv.read(MSCRATCH) == 0x00FF_FF00


def test_csrrc_clears_bits(csr_so) -> None:
    """CSRRC opcode (11): new = old & ~wdata."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    drv.write(MSCRATCH, 0xF0F0_F0F0)
    drv.clear(MSCRATCH, 0x0000_F0F0)
    assert drv.read(MSCRATCH) == 0xF0F0_0000


def test_hwif_out_reflects_sw_writes(csr_so) -> None:
    """Fields with `hw = r` or `hw = rw` are mirrored on hwif_out."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    drv.write(MSTATUS, 1 << 3)    # set MIE
    assert dut.hwif_out.mstatus_mie == 1
    drv.write(MSCRATCH, 0xA5A5_A5A5)
    assert dut.hwif_out.mscratch_value == 0xA5A5_A5A5


def test_hwif_in_drives_hw_writable_field(csr_so) -> None:
    """With no sw write in flight, hwif_in values latch into state on tick."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    dut.hwif_in.mepc_epc = 0xCAFE_0000
    tick(dut)
    assert drv.read(MEPC) == 0xCAFE_0000, \
        "hwif_in should drive mepc.epc when no sw write fires"


def test_read_enable_gates_rdata(csr_so) -> None:
    """`csr_rdata = csr_read_en ? mux : 0` — with read_en low, rdata is 0."""
    dut = fresh_dut(csr_so)
    reset(dut)
    drv = CsrPipelineDriver(dut)
    drv.write(MSCRATCH, 0xABCD_1234)
    dut.csr_addr = MSCRATCH
    dut.csr_read_en = 0
    dut.eval_comb()
    assert dut.csr_rdata == 0, "csr_rdata must be gated by csr_read_en"
    dut.csr_read_en = 1
    dut.eval_comb()
    assert dut.csr_rdata == 0xABCD_1234
