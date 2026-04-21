// Phase-6.6b mcycle / mcycleh swap-in verification.
//
// Exercises the new `riscv_hw_increment_when` counter path:
//
//   1. Read mcycle twice, snapshots apart; second reading must be
//      strictly larger (counter is running, cycle_en = 1 & ~inhibit).
//   2. Set mcountinhibit.cy (bit 0) to freeze the counter. Read
//      mcycle twice with nops between; both readings equal (frozen).
//   3. Clear mcountinhibit.cy to unfreeze. Read mcycle twice; second
//      must again exceed first.
//   4. csrw mcycle, 42 → reads back 42. Proves SW-write path works.
//   5. csrw mcycleh, 0xDEADBEEF → reads back 0xDEADBEEF. High half
//      is independently writable.

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

    // 1. Counter running — two successive reads, second > first.
    csrr   t0, 0xB00              // mcycle
    la     t1, snap_run_a
    sw     t0, 0(t1)
    nop
    nop
    nop
    nop
    csrr   t0, 0xB00
    la     t1, snap_run_b
    sw     t0, 0(t1)

    // 2. Freeze via mcountinhibit.cy, read twice, both equal.
    li     t0, (1 << 0)
    csrrs  zero, 0x320, t0        // mcountinhibit |= cy
    nop
    csrr   t0, 0xB00
    la     t1, snap_frozen_a
    sw     t0, 0(t1)
    nop
    nop
    nop
    nop
    csrr   t0, 0xB00
    la     t1, snap_frozen_b
    sw     t0, 0(t1)

    // 3. Unfreeze, confirm counter resumes.
    li     t0, (1 << 0)
    csrrc  zero, 0x320, t0        // mcountinhibit &= ~cy
    csrr   t0, 0xB00
    la     t1, snap_resumed_a
    sw     t0, 0(t1)
    nop
    nop
    nop
    nop
    csrr   t0, 0xB00
    la     t1, snap_resumed_b
    sw     t0, 0(t1)

    // 4. Re-freeze + SW write low half.
    li     t0, (1 << 0)
    csrrs  zero, 0x320, t0
    li     t0, 42
    csrw   0xB00, t0
    csrr   t0, 0xB00
    la     t1, snap_sw_write_low
    sw     t0, 0(t1)

    // 5. SW write high half.
    li     t0, 0xDEADBEEF
    csrw   0xB80, t0
    csrr   t0, 0xB80
    la     t1, snap_sw_write_high
    sw     t0, 0(t1)

    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    csrr   t0, mcause
    la     t1, unexpected_trap_mcause
    sw     t0, 0(t1)
trap_park:
    j      trap_park


.section .data
.globl snap_run_a
.globl snap_run_b
.globl snap_frozen_a
.globl snap_frozen_b
.globl snap_resumed_a
.globl snap_resumed_b
.globl snap_sw_write_low
.globl snap_sw_write_high
.globl done_marker
.globl unexpected_trap_mcause
.align 2
snap_run_a:             .word 0
snap_run_b:             .word 0
snap_frozen_a:          .word 0
snap_frozen_b:          .word 0
snap_resumed_a:         .word 0
snap_resumed_b:         .word 0
snap_sw_write_low:      .word 0
snap_sw_write_high:     .word 0
done_marker:            .word 0
unexpected_trap_mcause: .word 0
