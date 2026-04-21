// Phase-6.3 external-interrupt bring-up program (PLIC + claim/complete).
//
// Exercises the M-mode external-interrupt path end-to-end and proves
// our PLIC's claim/complete handshake works under a real RISC-V CPU:
//
//   _start configures PLIC for source 3 (priority 5, enabled on
//   context 0, threshold 0), arms mie.MEIE + mstatus.MIE, writes a
//   `ready_for_irq` sentinel into RAM, then busy-waits on `saw_trap`.
//
//   cocotb polls for `ready_for_irq`, then asserts
//   `ext_irq_sources_i[2] = 1` (SoC wires `plic.source_in[3] =
//   ext_irq_sources_i[2]`). Our generated PlicLogic arbitration picks
//   source 3 (only candidate), drives `intr_out[0]` high, which maps
//   to `ibex.irq_external_i → mip.MEIP`. Ibex traps on cause 11.
//
//   The cause-11 slot of the vector table jumps to
//   `common_trap_handler`. Handler:
//     1. stashes mcause/mepc/mip
//     2. reads PLIC.claim_0 — the register block pulses
//        `claim_0_read_pulse`, PlicLogic latches claimed[0][3] = 1 so
//        source 3 drops out of arbitration on this context
//     3. stores the claim ID into `saved_claim`
//     4. clears mie.MEIE as a belt-and-suspenders stop of re-fire
//     5. writes `claim_ack` = 1 to tell cocotb to drop the source
//     6. writes claim_id back to PLIC.claim_0 — the register block
//        pulses `claim_0_write_pulse`; PlicLogic clears claimed[0][3]
//        a cycle later (after the SW-written ID has propagated into
//        `hwif_out.claim_0_value`)
//     7. flags saw_trap, mrets
//
//   Main sees saw_trap, writes done_marker, halts.

#include "common.h"

.section .text.trap, "ax"
.globl trap_entry
.align 2
trap_entry:
#include "vector_table.inc"

.section .text.reset, "ax"
.globl _reset
.align 2
_reset:
    j   _start

.section .text, "ax"
.globl _start
.align 2
_start:
    la     sp, _stack_top

    la     t0, trap_entry
    csrw   mtvec, t0

    // PLIC: priority[3] = 5
    li     t0, PLIC_PRIORITY_BASE + 3*4
    li     t1, 5
    sw     t1, 0(t0)

    // PLIC: enable source 3 on context 0.
    li     t0, PLIC_ENABLE_0
    li     t1, (1 << 3)
    sw     t1, 0(t0)

    // PLIC: threshold[0] = 0 (anything priority > 0 can fire).
    li     t0, PLIC_THRESHOLD_0
    sw     zero, 0(t0)

    // Enable only the M-external interrupt in mie.
    li     t0, MIE_MEIE_MASK
    csrw   mie, t0

    // Tell cocotb the MMIO config is done — safe to raise source_in[3].
    la     t0, ready_for_irq
    li     t1, 1
    sw     t1, 0(t0)

    // Unleash traps.
    li     t0, MSTATUS_MIE_MASK
    csrs   mstatus, t0

    // Busy-wait for the ISR to flag.
    la     t0, saw_trap
1:  lw     t1, 0(t0)
    beqz   t1, 1b

    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    csrr   t0, mcause
    la     t1, saved_mcause
    sw     t0, 0(t1)

    csrr   t0, mepc
    la     t1, saved_mepc
    sw     t0, 0(t1)

    csrr   t0, mip
    la     t1, saved_mip
    sw     t0, 0(t1)

    // Read PLIC.claim_0. This:
    //   * returns the winning source ID (3 in this test)
    //   * pulses `claim_0_read_pulse`, causing our PlicLogic to latch
    //     `c0_claimed_r[3] = 1` — source 3 masked from arbitration on
    //     context 0 until the matching complete write.
    li     t0, PLIC_CLAIM_0
    lw     t1, 0(t0)
    la     t2, saved_claim
    sw     t1, 0(t2)

    // Clear mie.MEIE so even if something goes wrong with claim/
    // complete, Ibex won't storm back into this handler.
    li     t0, MIE_MEIE_MASK
    csrc   mie, t0

    // Tell cocotb it's safe to drop source_in. The source is masked
    // from arbitration anyway (thanks to the claim-read latch), so
    // this ordering doesn't affect correctness — it just lets the
    // test observe the "source deasserts between claim and complete"
    // case, closer to what a real IRQ-driven peripheral would do.
    la     t0, claim_ack
    li     t2, 1
    sw     t2, 0(t0)

    // Complete: write the same source ID back to PLIC.claim_0. Our
    // register block pulses `claim_0_write_pulse`; PlicLogic clears
    // `c0_claimed_r[3]` one cycle later (after the SW-written ID has
    // propagated through storage into `hwif_out.claim_0_value`).
    li     t0, PLIC_CLAIM_0
    sw     t1, 0(t0)

    // Flag main.
    la     t0, saw_trap
    li     t1, 1
    sw     t1, 0(t0)

    mret


.section .data
.globl saw_trap
.globl saved_mcause
.globl saved_mepc
.globl saved_mip
.globl saved_claim
.globl done_marker
.globl ready_for_irq
.globl claim_ack
.align 2
saw_trap:        .word 0
saved_mcause:    .word 0
saved_mepc:      .word 0
saved_mip:       .word 0
saved_claim:     .word 0
done_marker:     .word 0
ready_for_irq:   .word 0
claim_ack:       .word 0
