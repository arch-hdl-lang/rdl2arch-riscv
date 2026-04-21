"""Phase-6.3 end-to-end M-external interrupt + PLIC claim/complete.

Drives the full external-interrupt path on Ibex + our generated PLIC,
and proves the claim/complete handshake works when it's driven from
an actual RISC-V ISR rather than from an isolated unit sim:

  1. cocotb releases reset. `_start` configures PLIC for source 3
     (priority 5, enable_0 bit 3, threshold_0 = 0), arms mie.MEIE +
     mstatus.MIE, writes `ready_for_irq = 1` into RAM, busy-waits
     on `saw_trap`.
  2. cocotb polls RAM for `ready_for_irq`, then raises
     `ext_irq_sources_i[2]` — SoC wires that to `plic.source_in[3]`.
  3. PlicLogic arbitration picks source 3 → `intr_out[0]` high →
     `ibex.irq_external_i` → trap on cause 11.
  4. Vector slot 11 → common_trap_handler. Handler:
       * reads PLIC.claim_0 (auto-claims: PlicLogic sets
         claimed[0][3] = 1 via the read pulse)
       * stashes the claim ID into `saved_claim`
       * clears mie.MEIE (belt-and-suspenders against re-fire)
       * flags `claim_ack` for cocotb
       * writes the same ID back to PLIC.claim_0 (completes:
         PlicLogic clears claimed[0][3] one cycle later, after the
         write pulse is delayed and the SW-written ID has propagated
         into hwif_out.claim_0_value)
       * flags saw_trap, mrets
  5. cocotb sees claim_ack, drops the external source. (Program
     would still complete even without this — it just closes the
     loop visibly.)
  6. Main resumes, writes done_marker, halts. Test asserts the
     stashed mcause/mip/saved_claim values are correct.

Asserts:
  * saved_mcause  == 0x8000000B  (interrupt bit | cause 11)
  * saved_mip[11] == 1            (MEIP pending at trap entry)
  * saved_claim   == 3            (winning source ID returned by PLIC)
  * PlicLogic's post-complete intr_out goes low after source drops
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
SAVED_CLAIM     = 0x0010_1010
DONE_MARKER     = 0x0010_1014
READY_FOR_IRQ   = 0x0010_1018
CLAIM_ACK       = 0x0010_101C

START_PC        = 0x0010_0100
HALT_PC_UPPER   = 0x0010_0300

# SoC wires `plic.source_in[N] = ext_irq_sources_i[N-1]` for N in 1..8.
# Source 3 = bit 2 of the external-sources bus.
EXT_SRC_BIT_3   = 1 << 2


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
async def plic_external_irq_via_claim_complete(dut) -> None:
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

    irq_raised = False
    source_dropped = False
    for cycle in range(6000):
        await RisingEdge(dut.IO_CLK)

        # Wait for the program to finish configuring PLIC / setting
        # mie/mstatus before we raise the source line. Driving it too
        # early is fine (PlicLogic gates on enable & priority > thresh
        # & !claimed) but this keeps the ordering tidy and mirrors a
        # real boot handshake.
        if not irq_raised and _mem_word(dut, READY_FOR_IRQ) == 1:
            dut._log.info(f"cycle {cycle}: SW ready — raising ext_irq[2]")
            dut.ext_irq_sources_i.value = EXT_SRC_BIT_3
            irq_raised = True

        # ISR writes `claim_ack` between the claim read and the
        # complete write. At that moment the source is already masked
        # via PlicLogic's `claimed[0][3]` bit, so dropping source_in
        # here is safe.
        if (
            irq_raised
            and not source_dropped
            and _mem_word(dut, CLAIM_ACK) == 1
        ):
            dut._log.info(f"cycle {cycle}: ISR acked claim — dropping source")
            dut.ext_irq_sources_i.value = 0
            source_dropped = True

        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        saw         = _mem_word(dut, SAW_TRAP)
        mcause      = _mem_word(dut, SAVED_MCAUSE)
        mepc        = _mem_word(dut, SAVED_MEPC)
        mip         = _mem_word(dut, SAVED_MIP)
        claim       = _mem_word(dut, SAVED_CLAIM)
        ready       = _mem_word(dut, READY_FOR_IRQ)
        ack         = _mem_word(dut, CLAIM_ACK)
        extra: dict[str, object] = {}
        try:
            extra["ext_irq_sources_i"] = int(dut.ext_irq_sources_i.value)
            extra["plic_meip_o"]       = int(dut.plic_meip_o.value)
            plic = dut.u_plic_logic
            extra["c0_claimed_r"]      = int(plic.c0_claimed_r.value)
            csr = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i
            extra["mie_q"]     = int(csr.mie_q.value)
            extra["mstatus_q"] = int(csr.mstatus_q.value)
        except Exception as e:
            extra["error"] = repr(e)
        raise AssertionError(
            f"external ISR never completed after 6000 cycles;\n"
            f"  saw_trap={saw} saved_mcause={mcause:#x} "
            f"saved_mepc={mepc:#x} saved_mip={mip:#x}\n"
            f"  saved_claim={claim} ready_for_irq={ready} claim_ack={ack}\n"
            f"  final state: {extra}"
        )

    saved_mcause = _mem_word(dut, SAVED_MCAUSE)
    saved_mepc   = _mem_word(dut, SAVED_MEPC)
    saved_mip    = _mem_word(dut, SAVED_MIP)
    saved_claim  = _mem_word(dut, SAVED_CLAIM)

    # M-external interrupt: (1 << 31) | 11 = 0x8000000B.
    assert saved_mcause == 0x8000000B, (
        f"mcause: expected M-external 0x8000000B, got {saved_mcause:#x}"
    )

    # mip.MEIP is bit 11.
    assert (saved_mip >> 11) & 1 == 1, (
        f"mip at trap entry did not have MEIP set: {saved_mip:#x}"
    )

    # PLIC returned source 3 from the claim read.
    assert saved_claim == 3, (
        f"PLIC.claim_0 returned {saved_claim}, expected 3"
    )

    assert START_PC <= saved_mepc < HALT_PC_UPPER, (
        f"mepc {saved_mepc:#x} outside _start..halt window "
        f"[{START_PC:#x}, {HALT_PC_UPPER:#x})"
    )

    # After the complete write + source drop, PlicLogic should have
    # cleared `claimed[0][3]` and, with source_in low, should report
    # no winner → plic_meip_o = 0.
    assert int(dut.plic_meip_o.value) == 0, (
        "plic_meip_o still high after complete + source drop"
    )
