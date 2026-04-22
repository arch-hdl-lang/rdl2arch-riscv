// Phase-6.7 debug CSR swap-in verification.
//
// Debug CSRs (0x7B0–0x7B3) are architecturally inaccessible from
// M-mode — Ibex raises "illegal CSR" on csrr/csrw when
// `debug_mode_i = 0`. Our `DbgTriggerEn=0` SoC never enters debug
// mode, so there's no SW-visible exercise path from this program.
//
// The correctness of the 6.7 swap is attested by:
//   1. All pre-6.7 cpu programs still pass end-to-end (nothing
//      broke when the four `ibex_csr` instances went away).
//   2. The hybrid lints cleanly with `dcsr_q` / `depc_q` /
//      `dscratch{0,1}_q` references replaced by `hwif_out.*`.
//   3. Cocotb peeks the CsrFile storage hierarchically to confirm
//      reset values match the RDL (xdebugver=0x4, prv=M, rest=0).
//
// This program just runs to completion so the cocotb testbench
// can do the hierarchical peek after the "done" sentinel fires.

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
.globl done_marker
.globl unexpected_trap_mcause
.align 2
done_marker:             .word 0
unexpected_trap_mcause:  .word 0
