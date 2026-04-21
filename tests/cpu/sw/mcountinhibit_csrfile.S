// Phase-6.6a mcountinhibit swap-in verification.
//
// Focused SW-visible test of mcountinhibit storage. The RDL fixture
// models:
//   bit  0    cy     (sw=rw; hw=r)  — inhibit mcycle
//   bit  1    tm     (WPRI)         — reads 0, writes discarded
//   bit  2    ir     (sw=rw; hw=r)  — inhibit minstret
//   bits 3..12 hpm   (sw=rw; hw=r)  — inhibit mhpmcounter3..12
//   bits 13..31     (WPRI)          — reads 0, writes discarded
//
// Test steps:
//   1. Read mcountinhibit at reset  → 0.
//   2. csrw pattern with every WPRI bit set + every impl bit set
//      → readback returns only the implemented bits
//      (0x00001FFD: bits 12..3, 2, 0 — WPRI bits 1 and 13..31 drop).
//   3. csrrc ir (bit 2) → stored value loses bit 2 only.
//   4. csrrs tm (bit 1) via csrrs mcountinhibit, (1<<1) → bit 1 is
//      WPRI so stays 0; other bits unchanged.

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

    // 1. Reset readback.
    csrr   t0, 0x320              // mcountinhibit
    la     t1, snap_reset
    sw     t0, 0(t1)

    // 2. Write 0xFFFFFFFF — every bit. Only the implemented ones
    //    should stick (0x00001FFD = bits 0, 2..12).
    li     t0, 0xFFFFFFFF
    csrw   0x320, t0
    csrr   t0, 0x320
    la     t1, snap_all
    sw     t0, 0(t1)

    // 3. Clear ir (bit 2).
    li     t0, (1 << 2)
    csrrc  zero, 0x320, t0
    csrr   t0, 0x320
    la     t1, snap_ir_cleared
    sw     t0, 0(t1)

    // 4. Set tm (bit 1) — WPRI, should stay 0.
    li     t0, (1 << 1)
    csrrs  zero, 0x320, t0
    csrr   t0, 0x320
    la     t1, snap_after_tm_try
    sw     t0, 0(t1)

    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    // mstatus.MIE off the whole time — any trap is unexpected.
    csrr   t0, mcause
    la     t1, unexpected_trap_mcause
    sw     t0, 0(t1)
trap_park:
    j      trap_park


.section .data
.globl snap_reset
.globl snap_all
.globl snap_ir_cleared
.globl snap_after_tm_try
.globl done_marker
.globl unexpected_trap_mcause
.align 2
snap_reset:             .word 0
snap_all:               .word 0
snap_ir_cleared:        .word 0
snap_after_tm_try:      .word 0
done_marker:            .word 0
unexpected_trap_mcause: .word 0
