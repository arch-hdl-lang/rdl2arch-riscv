"""Phase-6.2 end-to-end timer-ISR check on the real Ibex SoC.

Released reset → Ibex fetches its reset vector at 0x0010_0080, which
jumps to `_start`. `_start` sets mtvec / mie.MTIE / mtimecmp /
mstatus.MIE, then busy-waits on the in-RAM `saw_trap` byte. Our
generated CLINT's mtime counter rolls past mtimecmp, `ClintLogic.
mtip_out` goes high, Ibex traps to `mtvec_base + 4*7 = 0x0010_001C`
(vectored mode is forced by Ibex — see the vector table in
`tests/cpu/sw/timer_isr.S`). The M-timer slot jumps to
`common_trap_handler`, which stashes mcause/mepc/mip into RAM, clears
mie.MTIE, flags `saw_trap`, and `mret`s. Main resumes, writes
`done_marker = 0xFEEDFACE`, and halts.

This test asserts:
  * `done_marker   == 0xFEEDFACE`  — ISR finished and main resumed.
  * `saved_mcause  == 0x80000007`  — interrupt bit | M-timer cause.
  * `saved_mip`[7] == 1            — MTIP was pending at trap entry.
  * `saved_mepc`   falls inside the busy-wait loop in `_start`.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


# RAM address → word index into `u_ram.u_ram.mem[]` (1 MB RAM, word-indexed).
# Must match the linker layout in `tests/cpu/sw/link.ld`.
RAM_BASE        = 0x0010_0000
SAW_TRAP        = 0x0010_1000
SAVED_MCAUSE    = 0x0010_1004
SAVED_MEPC      = 0x0010_1008
SAVED_MIP       = 0x0010_100C
DONE_MARKER     = 0x0010_1010

# `_start` runs from 0x0010_0100 (post–reset-trampoline). It busy-waits
# on `saw_trap` in the range 0x0010_0140..0x0010_0170 or so; we widen
# the expected mepc window to 0x200 bytes to stay future-proof against
# small optimizer changes.
START_PC        = 0x0010_0100
HALT_PC_UPPER   = 0x0010_0200


def _mem_word(dut, byte_addr: int) -> int:
    """Read one 32-bit word from the SoC's RAM via hierarchical access."""
    idx = (byte_addr - RAM_BASE) // 4
    return int(dut.u_ram.u_ram.mem[idx].value)


def _load_vmem(dut, path: str) -> int:
    """Poke the Verilator-backed RAM word-by-word from a $readmemh-style
    vmem file. Verilator's `-G<StringParam>` override for meminit is
    fiddly across versions; driving `mem[]` directly via VPI (enabled
    by `--public-flat-rw` at build time) is more portable and easier
    to debug when something goes wrong."""
    with open(path) as fh:
        count = 0
        for line in fh:
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            if line.startswith("@"):
                # vmem "@addr" markers would reposition the write head;
                # our `bin2vmem.py` never emits them, so blow up loudly
                # if one sneaks in.
                raise RuntimeError(
                    f"unexpected @addr line in {path}: {line!r}"
                )
            dut.u_ram.u_ram.mem[count].value = int(line, 16)
            count += 1
    return count


@cocotb.test()
async def timer_isr_fires_and_stashes_mcause(dut) -> None:
    # 10 ns clock period (100 MHz). Drive it continuously.
    cocotb.start_soon(Clock(dut.IO_CLK, 10, units="ns").start())

    # External PLIC sources tied off — this test only exercises the
    # CLINT timer path.
    dut.ext_irq_sources_i.value = 0

    # Ibex uses async active-low reset (`always_ff @(posedge clk or
    # negedge rst_ni)`); we need a clean 1→0 edge to fire the reset
    # branch, so start HIGH, pulse LOW, then release HIGH again. On
    # Verilator, driving high first forces rst_ni out of its X/Z-on-t0
    # default so the subsequent falling edge is deterministic.
    dut.IO_RST_N.value = 1
    await RisingEdge(dut.IO_CLK)
    dut.IO_RST_N.value = 0
    await RisingEdge(dut.IO_CLK)

    # Poke the program into RAM while the core is held in reset.
    vmem_path = os.environ["VMEM_PATH"]
    loaded = _load_vmem(dut, vmem_path)
    dut._log.info(f"loaded {loaded} words from {vmem_path}")

    # A few more reset cycles so downstream resets propagate.
    for _ in range(5):
        await RisingEdge(dut.IO_CLK)
    dut.IO_RST_N.value = 1

    # Ibex's program runs to completion in well under 500 cycles on a
    # warm RAM; 5000 is a wide margin that still keeps the sim fast.
    for _ in range(5000):
        await RisingEdge(dut.IO_CLK)
        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        # Test failed — pull the interesting state out of RAM + Ibex for
        # diagnosis and include it in the assertion message.
        mcause = _mem_word(dut, SAVED_MCAUSE)
        mepc   = _mem_word(dut, SAVED_MEPC)
        saw    = _mem_word(dut, SAW_TRAP)
        done   = _mem_word(dut, DONE_MARKER)
        extra: dict[str, object] = {}
        try:
            clint_regs = dut.u_clint_regblock
            extra["mtime_lo"]     = int(clint_regs.mtime_lo_r.value) & 0xFFFFFFFF
            extra["mtimecmp_lo"]  = int(clint_regs.mtimecmp_lo_r.value) & 0xFFFFFFFF
            extra["clint_mtip_o"] = int(dut.clint_mtip_o.value)
            csr = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i
            extra["mie_q"]      = int(csr.mie_q.value)
            extra["mstatus_q"]  = int(csr.mstatus_q.value)
            extra["mtvec_q"]    = int(csr.mtvec_q.value)
            extra["priv_lvl_q"] = int(csr.priv_lvl_q.value)
        except Exception as e:
            extra["error"] = repr(e)
        raise AssertionError(
            f"timer ISR never completed after 5000 cycles; "
            f"saw_trap={saw} saved_mcause={mcause:#x} "
            f"saved_mepc={mepc:#x} done_marker={done:#x}\n"
            f"final state: {extra}"
        )

    saved_mcause = _mem_word(dut, SAVED_MCAUSE)
    saved_mepc   = _mem_word(dut, SAVED_MEPC)
    saved_mip    = _mem_word(dut, SAVED_MIP)

    # RISC-V privileged-spec encoding for M-mode timer interrupt.
    assert saved_mcause == 0x80000007, (
        f"mcause: expected M-timer interrupt 0x80000007, got {saved_mcause:#x}"
    )

    # mip.MTIP is bit 7. Must have been pending at the moment we trapped.
    assert (saved_mip >> 7) & 1 == 1, (
        f"mip at trap entry did not have MTIP set: {saved_mip:#x}"
    )

    # mepc should point somewhere in the busy-wait loop in `_start`.
    # The exact instruction varies with optimizer mood, so we just
    # check it falls in the `.text` region between `_start` and halt.
    assert START_PC <= saved_mepc < HALT_PC_UPPER, (
        f"mepc {saved_mepc:#x} outside _start..halt window "
        f"[{START_PC:#x}, {HALT_PC_UPPER:#x})"
    )
