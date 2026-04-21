"""Phase-6.3 end-to-end M-software interrupt on Ibex + generated CLINT.

Program (`tests/cpu/sw/sw_isr.S`):
  _start sets mtvec, enables mie.MSIE + mstatus.MIE, writes 1 to
  CLINT.msip. Our generated ClintLogic drives msip_out high →
  ibex.irq_software_i → mip.MSIP = 1 → trap on cause 3. Handler
  stashes mcause/mepc/mip, writes 0 to CLINT.msip (acks), flags
  saw_trap, mrets. Main resumes, writes done_marker=0xFEEDFACE,
  halts.

Asserts:
  * `done_marker   == 0xFEEDFACE`  — ISR finished, main resumed.
  * `saved_mcause  == 0x80000003`  — interrupt bit | M-software cause.
  * `saved_mip`[3] == 1            — MSIP was pending at trap entry.
  * `saved_mepc`   inside `_start..halt`.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE        = 0x0010_0000
SAW_TRAP        = 0x0010_1000
SAVED_MCAUSE    = 0x0010_1004
SAVED_MEPC      = 0x0010_1008
SAVED_MIP       = 0x0010_100C
DONE_MARKER     = 0x0010_1010

START_PC        = 0x0010_0100
HALT_PC_UPPER   = 0x0010_0300


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
async def msip_fires_and_is_acked_by_writing_zero(dut) -> None:
    cocotb.start_soon(Clock(dut.IO_CLK, 10, units="ns").start())
    dut.ext_irq_sources_i.value = 0

    # Clean 1→0 edge so Ibex's async reset branch fires.
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

    for _ in range(5000):
        await RisingEdge(dut.IO_CLK)
        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        mcause = _mem_word(dut, SAVED_MCAUSE)
        mepc   = _mem_word(dut, SAVED_MEPC)
        saw    = _mem_word(dut, SAW_TRAP)
        done   = _mem_word(dut, DONE_MARKER)
        extra: dict[str, object] = {}
        try:
            clint_regs = dut.u_clint_regblock
            extra["msip_r"]       = int(clint_regs.msip_r.value) & 1
            extra["clint_msip_o"] = int(dut.clint_msip_o.value)
            csr = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i
            extra["mie_q"]      = int(csr.mie_q.value)
            extra["mstatus_q"]  = int(csr.mstatus_q.value)
            extra["priv_lvl_q"] = int(csr.priv_lvl_q.value)
        except Exception as e:
            extra["error"] = repr(e)
        raise AssertionError(
            f"msip ISR never completed after 5000 cycles; "
            f"saw_trap={saw} saved_mcause={mcause:#x} "
            f"saved_mepc={mepc:#x} done_marker={done:#x}\n"
            f"final state: {extra}"
        )

    saved_mcause = _mem_word(dut, SAVED_MCAUSE)
    saved_mepc   = _mem_word(dut, SAVED_MEPC)
    saved_mip    = _mem_word(dut, SAVED_MIP)

    # M-software interrupt: (1 << 31) | 3 = 0x80000003.
    assert saved_mcause == 0x80000003, (
        f"mcause: expected M-software 0x80000003, got {saved_mcause:#x}"
    )

    # mip.MSIP is bit 3.
    assert (saved_mip >> 3) & 1 == 1, (
        f"mip at trap entry did not have MSIP set: {saved_mip:#x}"
    )

    assert START_PC <= saved_mepc < HALT_PC_UPPER, (
        f"mepc {saved_mepc:#x} outside _start..halt window "
        f"[{START_PC:#x}, {HALT_PC_UPPER:#x})"
    )

    # The ISR cleared CLINT.msip by writing zero; the combinational
    # msip_out mirror should be low by the time we poll here.
    assert int(dut.clint_msip_o.value) == 0, (
        "clint_msip_o still high post-ack — was CLINT.msip cleared?"
    )
