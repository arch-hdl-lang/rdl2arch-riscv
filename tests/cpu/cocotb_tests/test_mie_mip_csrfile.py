"""Phase-6.5e end-to-end mie / mip swap-in validation.

The 4 interrupt-driven bring-up programs (timer/sw/ext/multictx) already
exercise `mie` end-to-end — each one does a `csrw mie, <bit>` that must
survive the round-trip + gate the Ibex controller correctly, or the trap
never fires. This test adds focused, SW-visible coverage on top:

  1. Read mie at reset         → 0
  2. csrrw mie, pattern        → readback keeps the 4 modelled bits
                                 (MSIE, MTIE, MEIE, MFIE[0]) and drops
                                 WPRI bits (which read as 0).
  3. csrrc mie, MTIE           → MTIE falls, rest hold.
  4. csrrs mie, MTIE           → MTIE comes back.
  5. Read mip                  → 0 (no IRQ source active).
  6. Write CLINT.msip = 1      → mip.MSIP mirrors to 1 (1-cycle-lagged
                                 hwif_in_live drive from irq_software_i).
  7. Write CLINT.msip = 0      → mip.MSIP falls back to 0.

We also hierarchically peek `mie_r` and `mip_r` inside the generated
`MTrapIbexCsrFile` to make sure the value observed by SW matches what's
actually stored (belt-and-suspenders for the bus/decode path).
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                   = 0x0010_0000
SNAP_MIE_RESET             = 0x0010_1000
SNAP_MIE_ALL               = 0x0010_1004
SNAP_MIE_MTIE_CLEARED      = 0x0010_1008
SNAP_MIE_MTIE_SET          = 0x0010_100C
SNAP_MIP_QUIET             = 0x0010_1010
SNAP_MIP_MSIP_RAISED       = 0x0010_1014
SNAP_MIP_MSIP_LOWERED      = 0x0010_1018
DONE_MARKER                = 0x0010_101C
UNEXPECTED_TRAP_MCAUSE     = 0x0010_1020


# Spec bit positions.
MSIE_BIT = 3
MTIE_BIT = 7
MEIE_BIT = 11
MFIE0_BIT = 16
MSIP_BIT = 3

ALL_ENABLE_BITS = (
    (1 << MSIE_BIT)
    | (1 << MTIE_BIT)
    | (1 << MEIE_BIT)
    | (1 << MFIE0_BIT)
)


def _mem_word(dut, byte_addr: int) -> int:
    idx = (byte_addr - RAM_BASE) // 4
    return int(dut.u_ram.u_ram.mem[idx].value)


def _load_vmem(dut, path: str) -> int:
    with open(path) as fh:
        count = 0
        for line in fh:
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            if line.startswith("@"):
                raise RuntimeError(f"unexpected @addr in {path}: {line!r}")
            dut.u_ram.u_ram.mem[count].value = int(line, 16)
            count += 1
    return count


@cocotb.test()
async def mie_mip_routes_through_csrfile(dut) -> None:
    cocotb.start_soon(Clock(dut.IO_CLK, 10, units="ns").start())
    dut.ext_irq_sources_i.value = 0

    dut.IO_RST_N.value = 1
    await RisingEdge(dut.IO_CLK)
    dut.IO_RST_N.value = 0
    await RisingEdge(dut.IO_CLK)

    vmem_path = os.environ["VMEM_PATH"]
    loaded = _load_vmem(dut, vmem_path)
    dut._log.info(f"loaded {loaded} words from {vmem_path}")

    for _ in range(5):
        await RisingEdge(dut.IO_CLK)
    dut.IO_RST_N.value = 1

    for _ in range(3000):
        await RisingEdge(dut.IO_CLK)
        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        raise AssertionError(
            "mie/mip program never completed. "
            f"snap_mie_reset={_mem_word(dut, SNAP_MIE_RESET):#x} "
            f"snap_mie_all={_mem_word(dut, SNAP_MIE_ALL):#x} "
            f"snap_mie_mtie_cleared="
            f"{_mem_word(dut, SNAP_MIE_MTIE_CLEARED):#x} "
            f"snap_mie_mtie_set={_mem_word(dut, SNAP_MIE_MTIE_SET):#x} "
            f"snap_mip_quiet={_mem_word(dut, SNAP_MIP_QUIET):#x} "
            f"snap_mip_msip_raised="
            f"{_mem_word(dut, SNAP_MIP_MSIP_RAISED):#x} "
            f"snap_mip_msip_lowered="
            f"{_mem_word(dut, SNAP_MIP_MSIP_LOWERED):#x} "
            f"unexpected_trap_mcause="
            f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # 1. mie out of reset — all modelled bits zero.
    reset_val = _mem_word(dut, SNAP_MIE_RESET)
    assert reset_val == 0, f"mie at reset should be 0, got {reset_val:#x}"

    # 2. After csrrw mie, (MSIE|MTIE|MEIE|MFIE[0]) — readback equals
    #    the pattern. WPRI bits were part of the SW write word but
    #    discarded by the RDL emit; here we only wrote modelled bits,
    #    so the whole word should round-trip cleanly.
    all_val = _mem_word(dut, SNAP_MIE_ALL)
    assert all_val == ALL_ENABLE_BITS, (
        f"mie after csrrw should be {ALL_ENABLE_BITS:#x}, got {all_val:#x}"
    )

    # 3. csrrc mie, (1<<MTIE) — MTIE clears, others hold.
    cleared_val = _mem_word(dut, SNAP_MIE_MTIE_CLEARED)
    expected_cleared = ALL_ENABLE_BITS & ~(1 << MTIE_BIT)
    assert cleared_val == expected_cleared, (
        f"mie after csrrc MTIE should be {expected_cleared:#x}, "
        f"got {cleared_val:#x}"
    )

    # 4. csrrs mie, (1<<MTIE) — MTIE comes back, others held.
    set_val = _mem_word(dut, SNAP_MIE_MTIE_SET)
    assert set_val == ALL_ENABLE_BITS, (
        f"mie after csrrs MTIE should be {ALL_ENABLE_BITS:#x}, "
        f"got {set_val:#x}"
    )

    # 5. mip with no source active — zero.
    quiet_val = _mem_word(dut, SNAP_MIP_QUIET)
    assert quiet_val == 0, (
        f"mip with no IRQ source should be 0, got {quiet_val:#x}"
    )

    # 6. After CLINT.msip = 1 — mip.MSIP is 1, other bits still 0.
    raised_val = _mem_word(dut, SNAP_MIP_MSIP_RAISED)
    assert raised_val == (1 << MSIP_BIT), (
        f"mip.MSIP should be set after CLINT.msip=1; got {raised_val:#x}, "
        f"expected {1 << MSIP_BIT:#x}"
    )

    # 7. After clearing CLINT.msip — mip.MSIP falls back.
    lowered_val = _mem_word(dut, SNAP_MIP_MSIP_LOWERED)
    assert lowered_val == 0, (
        f"mip.MSIP should clear after CLINT.msip=0; got {lowered_val:#x}"
    )

    # 8. Direct peek into CsrFile storage at program end.
    #    mie packed layout (first-declared = MSB):
    #      wpri_0_0(1) | wpri_2_1(2) | msie(1)   | wpri_6_4(3) |
    #      mtie(1)     | wpri_10_8(3)| meie(1)   | wpri_15_12(4)|
    #      mfie_0(1)   | wpri_hi(15)
    #    → msie at packed bit 28, mtie at 24, meie at 20, mfie_0 at 15.
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile
    mie_packed = int(cs.mie_r.value)
    file_msie  = (mie_packed >> 28) & 1
    file_mtie  = (mie_packed >> 24) & 1
    file_meie  = (mie_packed >> 20) & 1
    file_mfie0 = (mie_packed >> 15) & 1
    assert file_msie == 1, f"CsrFile mie.msie = {file_msie}, expected 1"
    assert file_mtie == 1, f"CsrFile mie.mtie = {file_mtie}, expected 1"
    assert file_meie == 1, f"CsrFile mie.meie = {file_meie}, expected 1"
    assert file_mfie0 == 1, (
        f"CsrFile mie.mfie_0 = {file_mfie0}, expected 1"
    )

    # mip packed layout mirrors mie's (same RDL shape). At program end
    # CLINT.msip has been cleared; irq_software_i is back to 0, and the
    # hwif_in_live drive has had 3 nops + a csrr + more instructions to
    # propagate, so mip_r.msip should be 0.
    mip_packed = int(cs.mip_r.value)
    file_msip = (mip_packed >> 28) & 1
    assert file_msip == 0, (
        f"CsrFile mip.msip = {file_msip} at end of program; "
        f"expected 0 after CLINT.msip was cleared"
    )
