"""Phase-6.5c SW-path validation for mepc / mcause / mtval swap-in.

The HW trap-save path for these three CSRs is implicitly covered
already: the 4 interrupt programs (timer/sw/ext/multictx) all do
`csrr t0, mcause; csrr t0, mepc` inside their common handler, and
those reads now all route through our generated CsrFile via the
cs_registers read mux. This test complements them by exercising
SW writes / reads directly:

  * round-trip via `csrrw` (WRITE) — verifies storage takes the
    value and subsequent `csrr` reads it back unchanged.
  * mepc WARL bitmask `0xFFFFFFFE` — an odd-valued write lands
    even in storage.
  * mcause spec layout — SW can write both interrupt-flagged
    (`0x8000000B`) and sync-cause values.
  * `csrrs` / `csrrc` set / clear semantics on mtval.

Also hierarchically peeks `u_ourfile.{mepc_r, mcause_r, mtval_r}`
to confirm the data really ended up in our generated CsrFile's
storage (not some Ibex shadow — there isn't one any more, it was
ripped out in the hybrid fork).
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                  = 0x0010_0000
SAVED_MEPC_ODD            = 0x0010_1000
SAVED_MEPC_EVEN           = 0x0010_1004
SAVED_MCAUSE_IRQ          = 0x0010_1008
SAVED_MCAUSE_EXC          = 0x0010_100C
SAVED_MTVAL_A             = 0x0010_1010
SAVED_MTVAL_SC            = 0x0010_1014
DONE_MARKER               = 0x0010_1018
UNEXPECTED_TRAP_MCAUSE    = 0x0010_101C


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
                raise RuntimeError(
                    f"unexpected @addr line in {path}: {line!r}"
                )
            dut.u_ram.u_ram.mem[count].value = int(line, 16)
            count += 1
    return count


@cocotb.test()
async def mepc_mcause_mtval_sw_path_lands_in_csrfile(dut) -> None:
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
            "program never completed. "
            f"unexpected_trap_mcause="
            f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x} "
            f"mepc_odd={_mem_word(dut, SAVED_MEPC_ODD):#x} "
            f"mepc_even={_mem_word(dut, SAVED_MEPC_EVEN):#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        f"unexpected trap: mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # mepc WARL: writing 0x12345679 should store 0x12345678.
    assert _mem_word(dut, SAVED_MEPC_ODD) == 0x12345678, (
        f"mepc odd-write WARL: got {_mem_word(dut, SAVED_MEPC_ODD):#x}, "
        f"expected 0x12345678 (bit 0 force-zero)"
    )
    assert _mem_word(dut, SAVED_MEPC_EVEN) == 0xCAFEBABE, (
        f"mepc even-write round-trip: got "
        f"{_mem_word(dut, SAVED_MEPC_EVEN):#x}, expected 0xCAFEBABE"
    )

    # mcause round-trips.
    assert _mem_word(dut, SAVED_MCAUSE_IRQ) == 0x8000000B, (
        f"mcause irq write: got {_mem_word(dut, SAVED_MCAUSE_IRQ):#x}, "
        f"expected 0x8000000B"
    )
    assert _mem_word(dut, SAVED_MCAUSE_EXC) == 0x00000002, (
        f"mcause exc write: got {_mem_word(dut, SAVED_MCAUSE_EXC):#x}, "
        f"expected 0x00000002"
    )

    # mtval round-trip.
    assert _mem_word(dut, SAVED_MTVAL_A) == 0xAAAA5555, (
        f"mtval round-trip: got {_mem_word(dut, SAVED_MTVAL_A):#x}"
    )
    # After csrrs/csrrc: start=0xFF00FF00, clear 0xFF000000, set 0x000000FF.
    # Expected: (0xFF00FF00 & ~0xFF000000) | 0x000000FF = 0x0000FFFF.
    assert _mem_word(dut, SAVED_MTVAL_SC) == 0x0000FFFF, (
        f"mtval csrrs/csrrc: got {_mem_word(dut, SAVED_MTVAL_SC):#x}, "
        f"expected 0x0000FFFF"
    )

    # Direct hierarchical peek into the generated CsrFile's storage
    # at end of program. Final values:
    #   mepc   = 0xCAFEBABE     (even-write round-trip winner)
    #   mcause = 0x00000002     (exc-cause winner)
    #   mtval  = 0x0000FFFF     (csrrs/csrrc result)
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile

    mepc = int(cs.mepc_r.value)
    assert mepc == 0xCAFEBABE, (
        f"CsrFile mepc_r = {mepc:#x}, expected 0xCAFEBABE"
    )

    mcause = int(cs.mcause_r.value)
    assert mcause == 0x00000002, (
        f"CsrFile mcause_r = {mcause:#x}, expected 0x00000002"
    )

    mtval = int(cs.mtval_r.value)
    assert mtval == 0x0000FFFF, (
        f"CsrFile mtval_r = {mtval:#x}, expected 0x0000FFFF"
    )
