"""Phase-6.6b end-to-end mcycle / mcycleh swap-in validation.

Confirms the new `riscv_hw_increment_when` generator feature lands in
the Ibex hybrid: the cycle counter lives inside the CsrFile, auto-
increments when `cycle_en` (wired from `mhpmcounter_incr[0] &
~mcountinhibit[0]` on the Ibex side) is high, and SW writes take
priority over the increment on the same cycle.

Covered paths:
  * Counter running — two successive reads separated by nops; second
    value strictly larger.
  * mcountinhibit.cy gates the increment — two reads while frozen
    return the same value.
  * Unfreezing resumes counting.
  * SW csrw mcycle takes effect even while frozen.
  * mcycleh is independently writable.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                 = 0x0010_0000
SNAP_RUN_A               = 0x0010_1000
SNAP_RUN_B               = 0x0010_1004
SNAP_FROZEN_A            = 0x0010_1008
SNAP_FROZEN_B            = 0x0010_100C
SNAP_RESUMED_A           = 0x0010_1010
SNAP_RESUMED_B           = 0x0010_1014
SNAP_SW_WRITE_LOW        = 0x0010_1018
SNAP_SW_WRITE_HIGH       = 0x0010_101C
DONE_MARKER              = 0x0010_1020
UNEXPECTED_TRAP_MCAUSE   = 0x0010_1024


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
async def mcycle_routes_through_csrfile(dut) -> None:
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
            "mcycle program never completed. "
            f"snap_run_a={_mem_word(dut, SNAP_RUN_A):#x} "
            f"snap_run_b={_mem_word(dut, SNAP_RUN_B):#x} "
            f"snap_frozen_a={_mem_word(dut, SNAP_FROZEN_A):#x} "
            f"snap_frozen_b={_mem_word(dut, SNAP_FROZEN_B):#x} "
            f"unexpected_trap_mcause="
            f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # 1. Counter running — second read strictly exceeds first.
    run_a = _mem_word(dut, SNAP_RUN_A)
    run_b = _mem_word(dut, SNAP_RUN_B)
    assert run_b > run_a, (
        f"running counter should advance: run_a={run_a:#x}, run_b={run_b:#x}"
    )

    # 2. Frozen counter — both reads equal.
    frozen_a = _mem_word(dut, SNAP_FROZEN_A)
    frozen_b = _mem_word(dut, SNAP_FROZEN_B)
    assert frozen_a == frozen_b, (
        f"frozen counter should hold: frozen_a={frozen_a:#x}, "
        f"frozen_b={frozen_b:#x}"
    )

    # 3. Resumed counter — second read exceeds first.
    resumed_a = _mem_word(dut, SNAP_RESUMED_A)
    resumed_b = _mem_word(dut, SNAP_RESUMED_B)
    assert resumed_b > resumed_a, (
        f"resumed counter should advance: resumed_a={resumed_a:#x}, "
        f"resumed_b={resumed_b:#x}"
    )

    # 4. SW write mcycle = 42 (while frozen, so it holds).
    sw_low = _mem_word(dut, SNAP_SW_WRITE_LOW)
    assert sw_low == 42, (
        f"mcycle after csrw 42 should be 42, got {sw_low}"
    )

    # 5. SW write mcycleh = 0xDEADBEEF.
    sw_high = _mem_word(dut, SNAP_SW_WRITE_HIGH)
    assert sw_high == 0xDEADBEEF, (
        f"mcycleh after csrw 0xDEADBEEF should be 0xDEADBEEF, "
        f"got {sw_high:#x}"
    )

    # 6. Direct hierarchical peek — mcycle_r.value equals SW-written 42.
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile
    file_mcycle  = int(cs.mcycle_r.value)
    file_mcycleh = int(cs.mcycleh_r.value)
    assert file_mcycle == 42, (
        f"CsrFile mcycle_r.value = {file_mcycle}, expected 42"
    )
    assert file_mcycleh == 0xDEADBEEF, (
        f"CsrFile mcycleh_r.value = {file_mcycleh:#x}, expected 0xDEADBEEF"
    )
