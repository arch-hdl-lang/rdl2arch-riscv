"""Phase-6.4 end-to-end multi-context PLIC on Ibex.

Drives both PLIC contexts on the same run, proving per-context
independence of claimed_r + correct routing through the SoC:

  ctx 0 → ibex.irq_external_i → cause 11 (mip.MEIP)
  ctx 1 → ibex.irq_fast_i[0]   → cause 16 (mip bit 16)

Program (`tests/cpu/sw/multictx_isr.S`) configures:
  source 3 @ priority 5, enabled on ctx 0 only
  source 5 @ priority 5, enabled on ctx 1 only
  mie = MEIE | (1 << 16)

Cocotb polls `ready_for_irq`, then drives BOTH `ext_irq_sources_i[2]`
(→ source_in[3]) and `ext_irq_sources_i[4]` (→ source_in[5]). Ibex
prioritises M-external first, so it traps on cause 11, handler claims
PLIC context 0, services source 3, completes, clears MEIE, mrets.
Returns to main's busy-wait; fast[0] is still pending, so Ibex
immediately takes cause 16, handler does the same for context 1.

Asserts:
  * saved_mcause_ctx0 == 0x8000000B
  * saved_mcause_ctx1 == 0x80000010
  * saved_claim_ctx0  == 3   (source 3 returned by PLIC ctx 0)
  * saved_claim_ctx1  == 5   (source 5 returned by PLIC ctx 1)
  * saved_bad_cause    == 0   (never hit the unexpected-cause path)
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE             = 0x0010_0000
CTX0_FIRED           = 0x0010_1000
CTX1_FIRED           = 0x0010_1004
SAVED_MCAUSE_CTX0    = 0x0010_1008
SAVED_MEPC_CTX0      = 0x0010_100C
SAVED_CLAIM_CTX0     = 0x0010_1010
SAVED_MCAUSE_CTX1    = 0x0010_1014
SAVED_MEPC_CTX1      = 0x0010_1018
SAVED_CLAIM_CTX1     = 0x0010_101C
SAVED_BAD_CAUSE      = 0x0010_1020
DONE_MARKER          = 0x0010_1024
READY_FOR_IRQ        = 0x0010_1028

START_PC        = 0x0010_0100
HALT_PC_UPPER   = 0x0010_0300

# SoC wires plic.source_in[N] = ext_irq_sources_i[N-1] for N in 1..8.
EXT_SRC_BIT_3 = 1 << 2       # source id 3
EXT_SRC_BIT_5 = 1 << 4       # source id 5


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
async def plic_two_contexts_fire_and_are_serviced_independently(dut) -> None:
    cocotb.start_soon(Clock(dut.IO_CLK, 10, units="ns").start())
    dut.ext_irq_sources_i.value = 0

    # Clean 1→0 reset edge.
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

    sources_raised = False
    for cycle in range(8000):
        await RisingEdge(dut.IO_CLK)

        if not sources_raised and _mem_word(dut, READY_FOR_IRQ) == 1:
            dut._log.info(
                f"cycle {cycle}: SW ready — raising ext_irq[2] + [4] "
                f"(sources 3, 5)"
            )
            dut.ext_irq_sources_i.value = EXT_SRC_BIT_3 | EXT_SRC_BIT_5
            sources_raised = True

        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        ctx0 = _mem_word(dut, CTX0_FIRED)
        ctx1 = _mem_word(dut, CTX1_FIRED)
        bad  = _mem_word(dut, SAVED_BAD_CAUSE)
        extra: dict[str, object] = {}
        try:
            extra["plic_meip_o"]      = int(dut.plic_meip_o.value)
            extra["plic_ctx1_irq_o"]  = int(dut.plic_ctx1_irq_o.value)
            plic = dut.u_plic_logic
            extra["c0_claimed_r"]     = int(plic.c0_claimed_r.value)
            extra["c1_claimed_r"]     = int(plic.c1_claimed_r.value)
            csr = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i
            extra["mie_q"]      = int(csr.mie_q.value)
            extra["mstatus_q"]  = int(csr.mstatus_q.value)
        except Exception as e:
            extra["error"] = repr(e)
        raise AssertionError(
            f"multictx ISR never completed after 8000 cycles; "
            f"ctx0_fired={ctx0} ctx1_fired={ctx1} bad_cause={bad:#x}\n"
            f"final state: {extra}"
        )

    # Both contexts must have flagged.
    assert _mem_word(dut, CTX0_FIRED) == 1, "ctx0 never fired"
    assert _mem_word(dut, CTX1_FIRED) == 1, "ctx1 never fired"
    assert _mem_word(dut, SAVED_BAD_CAUSE) == 0, (
        f"unexpected cause: {_mem_word(dut, SAVED_BAD_CAUSE):#x}"
    )

    # mcause encoding: (1 << 31) | cause.
    assert _mem_word(dut, SAVED_MCAUSE_CTX0) == 0x8000000B, (
        f"ctx0 mcause: expected 0x8000000B (M-external), got "
        f"{_mem_word(dut, SAVED_MCAUSE_CTX0):#x}"
    )
    assert _mem_word(dut, SAVED_MCAUSE_CTX1) == 0x80000010, (
        f"ctx1 mcause: expected 0x80000010 (fast[0]), got "
        f"{_mem_word(dut, SAVED_MCAUSE_CTX1):#x}"
    )

    # PLIC returned the right winning source IDs to each context.
    # ctx 0 is configured to see only source 3; ctx 1 only source 5.
    assert _mem_word(dut, SAVED_CLAIM_CTX0) == 3, (
        f"ctx0 claim: expected source 3, got "
        f"{_mem_word(dut, SAVED_CLAIM_CTX0)}"
    )
    assert _mem_word(dut, SAVED_CLAIM_CTX1) == 5, (
        f"ctx1 claim: expected source 5, got "
        f"{_mem_word(dut, SAVED_CLAIM_CTX1)}"
    )

    # Both mepc values should land inside the busy-wait loop in _start.
    for sym, label in (
        (SAVED_MEPC_CTX0, "ctx0"),
        (SAVED_MEPC_CTX1, "ctx1"),
    ):
        mepc = _mem_word(dut, sym)
        assert START_PC <= mepc < HALT_PC_UPPER, (
            f"{label} mepc {mepc:#x} outside busy-wait window"
        )
