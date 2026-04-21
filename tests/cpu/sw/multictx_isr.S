// Phase-6.4 multi-context PLIC bring-up program.
//
// Exercises both PLIC contexts on the SoC end-to-end. The SoC wires
// `plic.intr_out[0]` to Ibex's `irq_external_i` (mip.MEIP, cause 11)
// and `plic.intr_out[1]` to `irq_fast_i[0]` (mip bit 16, cause 16 —
// Ibex is M-only so there's no S-mode "SEIP" pin; fast[0] is our
// stand-in for the second context).
//
// Setup:
//   source 3, priority 5, enable_0 bit 3, threshold_0 = 0   → ctx 0
//   source 5, priority 5, enable_1 bit 5, threshold_1 = 0   → ctx 1
//   mie = MEIE | fast[0]    (bits 11 and 16)
//   mstatus.MIE = 1
//
// Cocotb polls `ready_for_irq`, then drives both source lines at
// once. Ibex prioritises M-external over fast IRQs, so the order of
// traps is:
//
//   1. cause 11 trap → handler claims PLIC.claim_0, completes,
//      clears mie.MEIE so ctx 0 won't re-fire, sets ctx0_fired = 1.
//      mret returns to main's busy-wait.
//   2. main re-enters busy-wait; fast[0] is still pending, so Ibex
//      takes the second trap almost immediately.
//   3. cause 16 trap → handler claims PLIC.claim_1, completes,
//      clears mie[fast[0]] so ctx 1 won't re-fire, sets
//      ctx1_fired = 1. mret.
//   4. main sees both *_fired flags, writes done_marker, halts.
//
// Per-context independence assertion: `saved_claim_ctx0 == 3` and
// `saved_claim_ctx1 == 5`, distinct registers in distinct PLIC
// contexts.

#include "common.h"

#define MIE_MFIE0_MASK          (1 << 16)

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

    // PLIC: priority[3] = 5, priority[5] = 5.
    li     t0, PLIC_PRIORITY_BASE + 3*4
    li     t1, 5
    sw     t1, 0(t0)
    li     t0, PLIC_PRIORITY_BASE + 5*4
    sw     t1, 0(t0)

    // Enable source 3 on ctx 0 only.
    li     t0, PLIC_ENABLE_0
    li     t1, (1 << 3)
    sw     t1, 0(t0)

    // Enable source 5 on ctx 1 only.
    li     t0, PLIC_ENABLE_1
    li     t1, (1 << 5)
    sw     t1, 0(t0)

    // Both thresholds at 0 (priority 5 > 0, so anything fires).
    li     t0, PLIC_THRESHOLD_0
    sw     zero, 0(t0)
    li     t0, PLIC_THRESHOLD_1
    sw     zero, 0(t0)

    // Enable M-external (ctx 0) and fast[0] (ctx 1).
    li     t0, MIE_MEIE_MASK | MIE_MFIE0_MASK
    csrw   mie, t0

    // Tell cocotb it's safe to drive both source lines.
    la     t0, ready_for_irq
    li     t1, 1
    sw     t1, 0(t0)

    // Unleash traps.
    li     t0, MSTATUS_MIE_MASK
    csrs   mstatus, t0

    // Busy-wait for BOTH contexts to fire.
    la     t0, ctx0_fired
    la     t1, ctx1_fired
1:  lw     t2, 0(t0)
    beqz   t2, 1b
    lw     t2, 0(t1)
    beqz   t2, 1b

    // Both fired — write the completion sentinel and halt.
    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    // Dispatch on mcause[30:0]: the interrupt bit (mcause[31]) is
    // guaranteed set here because the vector table only ever jumps
    // into this handler for interrupts (exceptions would land on
    // cause 0 which we don't expect in this test).
    csrr   t0, mcause
    // Mask off the interrupt bit to get the cause number.
    slli   t1, t0, 1       // shift out bit 31
    srli   t1, t1, 1
    li     t2, 11          // M-external = cause 11
    beq    t1, t2, handle_ctx0
    li     t2, 16          // fast[0]    = cause 16
    beq    t1, t2, handle_ctx1
    // Unexpected cause — park and let cocotb observe the failure.
    la     t0, saved_bad_cause
    csrr   t1, mcause
    sw     t1, 0(t0)
bad_loop:
    j      bad_loop

handle_ctx0:
    // Stash cause + mepc for cocotb.
    la     t1, saved_mcause_ctx0
    sw     t0, 0(t1)
    csrr   t1, mepc
    la     t2, saved_mepc_ctx0
    sw     t1, 0(t2)

    // Claim + complete on PLIC context 0.
    li     t0, PLIC_CLAIM_0
    lw     t1, 0(t0)                   // claim
    la     t2, saved_claim_ctx0
    sw     t1, 0(t2)
    sw     t1, 0(t0)                   // complete

    // Disable M-external so ctx 0 won't re-fire even if source stays high.
    li     t0, MIE_MEIE_MASK
    csrc   mie, t0

    la     t0, ctx0_fired
    li     t1, 1
    sw     t1, 0(t0)
    mret

handle_ctx1:
    la     t1, saved_mcause_ctx1
    sw     t0, 0(t1)
    csrr   t1, mepc
    la     t2, saved_mepc_ctx1
    sw     t1, 0(t2)

    // Claim + complete on PLIC context 1.
    li     t0, PLIC_CLAIM_1
    lw     t1, 0(t0)                   // claim
    la     t2, saved_claim_ctx1
    sw     t1, 0(t2)
    sw     t1, 0(t0)                   // complete

    // Disable fast[0] so ctx 1 won't re-fire.
    li     t0, MIE_MFIE0_MASK
    csrc   mie, t0

    la     t0, ctx1_fired
    li     t1, 1
    sw     t1, 0(t0)
    mret


.section .data
.globl ctx0_fired
.globl ctx1_fired
.globl saved_mcause_ctx0
.globl saved_mepc_ctx0
.globl saved_claim_ctx0
.globl saved_mcause_ctx1
.globl saved_mepc_ctx1
.globl saved_claim_ctx1
.globl saved_bad_cause
.globl done_marker
.globl ready_for_irq
.align 2
ctx0_fired:         .word 0
ctx1_fired:         .word 0
saved_mcause_ctx0:  .word 0
saved_mepc_ctx0:    .word 0
saved_claim_ctx0:   .word 0
saved_mcause_ctx1:  .word 0
saved_mepc_ctx1:    .word 0
saved_claim_ctx1:   .word 0
saved_bad_cause:    .word 0
done_marker:        .word 0
ready_for_irq:      .word 0
