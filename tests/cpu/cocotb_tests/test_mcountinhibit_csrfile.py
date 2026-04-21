"""Phase-6.6a end-to-end mcountinhibit swap-in validation.

Confirms that `mcountinhibit` (CSR 0x320) storage has moved from
Ibex's upstream `mcountinhibit_q` flop to our generated
`MTrapIbexCsrFile`. Exercised paths:

  * csrw with all-ones mask     → only implemented bits (0, 2..12)
                                    stick; bit 1 (tm, WPRI) + bits
                                    13..31 (WPRI) drop.
  * csrrc clearing bit 2 (ir)   → only bit 2 drops; 0 + 3..12 hold.
  * csrrs on bit 1 (tm, WPRI)   → stays zero; other bits unchanged.

The hybrid adapter consumes the whole register as
`hwif_out.mcountinhibit_rdata_flat` and feeds it back to the HPM
counter-gating logic on the Ibex side — so the HPM counters still
work off the same 32-bit inhibit view upstream had.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                 = 0x0010_0000
SNAP_RESET               = 0x0010_1000
SNAP_ALL                 = 0x0010_1004
SNAP_IR_CLEARED          = 0x0010_1008
SNAP_AFTER_TM_TRY        = 0x0010_100C
DONE_MARKER              = 0x0010_1010
UNEXPECTED_TRAP_MCAUSE   = 0x0010_1014


# Implemented bits on our Ibex config (MHPMCounterNum=10):
#   bit 0       cy
#   bit 2       ir
#   bits 3..12  hpm3..hpm12   (10 bits)
# Total mask: 0x0000_1FFD.
IMPL_MASK = (1 << 0) | (1 << 2) | ((0x3FF) << 3)


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
async def mcountinhibit_routes_through_csrfile(dut) -> None:
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
            "mcountinhibit program never completed. "
            f"snap_reset={_mem_word(dut, SNAP_RESET):#x} "
            f"snap_all={_mem_word(dut, SNAP_ALL):#x} "
            f"snap_ir_cleared={_mem_word(dut, SNAP_IR_CLEARED):#x} "
            f"snap_after_tm_try={_mem_word(dut, SNAP_AFTER_TM_TRY):#x} "
            f"unexpected_trap_mcause="
            f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # 1. Reset value is 0.
    reset_val = _mem_word(dut, SNAP_RESET)
    assert reset_val == 0, f"mcountinhibit at reset should be 0, got {reset_val:#x}"

    # 2. After csrw 0xFFFFFFFF — only implemented bits stick.
    all_val = _mem_word(dut, SNAP_ALL)
    assert all_val == IMPL_MASK, (
        f"csrw 0xFFFFFFFF should yield {IMPL_MASK:#x} (impl bits only), "
        f"got {all_val:#x}"
    )

    # 3. csrrc ir (bit 2) — only bit 2 drops, rest hold.
    ir_cleared_val = _mem_word(dut, SNAP_IR_CLEARED)
    expected_after_ir = IMPL_MASK & ~(1 << 2)
    assert ir_cleared_val == expected_after_ir, (
        f"csrrc (1<<2) should yield {expected_after_ir:#x}, got "
        f"{ir_cleared_val:#x}"
    )

    # 4. csrrs tm (bit 1) — WPRI, stays 0; rest unchanged.
    after_tm_val = _mem_word(dut, SNAP_AFTER_TM_TRY)
    assert after_tm_val == expected_after_ir, (
        f"csrrs (1<<1) on WPRI bit should leave storage unchanged at "
        f"{expected_after_ir:#x}, got {after_tm_val:#x}"
    )

    # 5. Direct hierarchical peek into CsrFile storage.
    #
    # Packed layout (first-declared = MSB):
    #   cy(1) | reserved_tm(1) | ir(1) | hpm(10) | reserved_hi(19)
    # → cy at packed bit 31, tm at 30, ir at 29, hpm[9:0] at 28..19.
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile
    packed = int(cs.mcountinhibit_r.value)
    file_cy  = (packed >> 31) & 1
    file_tm  = (packed >> 30) & 1
    file_ir  = (packed >> 29) & 1
    file_hpm = (packed >> 19) & 0x3FF
    # After step 3, cy=1, tm=0, ir=0, hpm=0x3FF.
    assert file_cy == 1, f"CsrFile cy = {file_cy}, expected 1"
    assert file_tm == 0, f"CsrFile tm = {file_tm}, expected 0 (WPRI)"
    assert file_ir == 0, f"CsrFile ir = {file_ir}, expected 0"
    assert file_hpm == 0x3FF, (
        f"CsrFile hpm = {file_hpm:#x}, expected 0x3FF"
    )
