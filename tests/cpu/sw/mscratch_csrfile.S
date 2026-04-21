// Phase-6.5a mscratch swap-in verification.
//
// After Phase 6.5a, the forked `ibex_cs_registers_hybrid.sv` routes
// every access to CSR 0x340 through our generated
// `MTrapMscratchCsrFile` — Ibex's internal `u_mscratch_csr` storage
// was removed. This program exercises that path from RISC-V:
//
//   1. Write pattern_a = 0xC0FFEE42 to mscratch.
//   2. Read mscratch back into t0; store t0 to saved_readback_a.
//   3. Write pattern_b = 0xDEADBEEF to mscratch.
//   4. Read it back into t0; store to saved_readback_b.
//   5. Write done_marker = 0xFEEDFACE; halt.
//
// Cocotb then asserts:
//   * saved_readback_a == 0xC0FFEE42
//   * saved_readback_b == 0xDEADBEEF
//   * the backing store of
//     u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile.mscratch_r.value
//     equals pattern_b at the end — proving the value landed in OUR
//     CsrFile's state, not in any upstream-Ibex shadow.

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

    // mtvec still has to be sane even without interrupts enabled —
    // any stray trap would hang.
    la     t0, trap_entry
    csrw   mtvec, t0

    // ── round A ────────────────────────────────────────────────
    li     t0, 0xC0FFEE42
    csrw   mscratch, t0            // csrrw x0, mscratch, t0 → CsrFile write

    csrr   t1, mscratch            // csrrs t1, mscratch, x0 → CsrFile read
    la     t2, saved_readback_a
    sw     t1, 0(t2)

    // ── round B ────────────────────────────────────────────────
    li     t0, 0xDEADBEEF
    csrw   mscratch, t0

    csrr   t1, mscratch
    la     t2, saved_readback_b
    sw     t1, 0(t2)

    // ── done ───────────────────────────────────────────────────
    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    // No interrupts are enabled in this test — any trap is a bug.
    // Park in a loop with mcause stashed so cocotb can surface it.
    csrr   t0, mcause
    la     t1, unexpected_trap_mcause
    sw     t0, 0(t1)
trap_park:
    j      trap_park


.section .data
.globl saved_readback_a
.globl saved_readback_b
.globl done_marker
.globl unexpected_trap_mcause
.align 2
saved_readback_a:        .word 0
saved_readback_b:        .word 0
done_marker:             .word 0
unexpected_trap_mcause:  .word 0
