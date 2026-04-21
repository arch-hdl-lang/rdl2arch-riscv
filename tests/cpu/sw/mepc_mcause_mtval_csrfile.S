// Phase-6.5c mepc / mcause / mtval swap-in verification.
//
// Focused SW-path check. The HW trap-save path for these three CSRs
// is already proven end-to-end by the 4 interrupt programs
// (timer/sw/ext/multictx) — their common_trap_handler reads mcause +
// mepc after every trap, and those reads now all route through our
// CsrFile. This program exercises the complementary paths:
//
//   * `csrrw` writes from SW land in our CsrFile's storage (not
//     some leftover Ibex shadow).
//   * `csrrs` / `csrrc` SET / CLEAR semantics match spec.
//   * WARL legalization on mepc.epc (bit 0 force-zero) applies.
//
// No interrupts are enabled — any trap here is a bug. mtvec is set
// to `trap_entry` so such a bug parks in a debugging loop rather
// than fetching from PC=0.

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

    // ── mepc round-trip ────────────────────────────────────────
    // WARL `0xFFFFFFFE` masks bit 0 to zero. Write odd value; read
    // should return even.
    li     t0, 0x12345679                   // odd
    csrw   mepc, t0
    csrr   t1, mepc
    la     t2, saved_mepc_odd
    sw     t1, 0(t2)

    li     t0, 0xCAFEBABE                   // even
    csrw   mepc, t0
    csrr   t1, mepc
    la     t2, saved_mepc_even
    sw     t1, 0(t2)

    // ── mcause round-trip ──────────────────────────────────────
    // Spec says mcause is a full 32-bit WARL register (Ibex drops
    // the middle bits on its packed encoding; our CsrFile stores
    // the flat 32-bit value). SW writes survive round-trip in our
    // CsrFile — any discrepancy with Ibex's stricter shape is OK
    // for this test.
    li     t0, 0x8000000B                   // M-external interrupt
    csrw   mcause, t0
    csrr   t1, mcause
    la     t2, saved_mcause_irq
    sw     t1, 0(t2)

    li     t0, 0x00000002                   // illegal-instruction sync
    csrw   mcause, t0
    csrr   t1, mcause
    la     t2, saved_mcause_exc
    sw     t1, 0(t2)

    // ── mtval round-trip ───────────────────────────────────────
    li     t0, 0xAAAA5555
    csrw   mtval, t0
    csrr   t1, mtval
    la     t2, saved_mtval_a
    sw     t1, 0(t2)

    // ── csrrs / csrrc via clear-then-set on mtval ──────────────
    // Start from 0xFF00FF00, clear the upper byte, set the bottom
    // two nibbles. End value: 0x00_00_FF_FF.
    li     t0, 0xFF00FF00
    csrw   mtval, t0
    li     t0, 0xFF000000                   // clear mask
    csrrc  zero, mtval, t0
    li     t0, 0x000000FF                   // set mask
    csrrs  zero, mtval, t0
    csrr   t1, mtval
    la     t2, saved_mtval_sc
    sw     t1, 0(t2)

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
.globl saved_mepc_odd
.globl saved_mepc_even
.globl saved_mcause_irq
.globl saved_mcause_exc
.globl saved_mtval_a
.globl saved_mtval_sc
.globl done_marker
.globl unexpected_trap_mcause
.align 2
saved_mepc_odd:          .word 0
saved_mepc_even:         .word 0
saved_mcause_irq:        .word 0
saved_mcause_exc:        .word 0
saved_mtval_a:           .word 0
saved_mtval_sc:          .word 0
done_marker:             .word 0
unexpected_trap_mcause:  .word 0
