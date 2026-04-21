// Phase-6.5d mstatus swap-in verification.
//
// The 4 interrupt programs (timer/sw/ext/multictx) already exercise
// the full mstatus flow end-to-end under this phase:
//
//   csrs mstatus, (1<<3)        → SW write via bus → CsrFile stores mie=1
//   trap fires / csr_save_cause → adapter clears mie, TrapCoord saves mpie/mpp
//   handler reads/writes        → nested traps blocked by mie=0
//   mret / csr_restore_mret     → adapter restores mie/mpie/mpp
//
// This program is a focused unit-style check of the state transitions
// from SW observation:
//
//   1. Clear mstatus.MIE from reset.
//   2. Read mstatus → expect mie=0.
//   3. csrs mstatus.MIE via bit set.
//   4. Read mstatus → expect mie=1 (bit 3 set).
//   5. Trigger a synchronous exception by issuing `ecall` → cause 11.
//      Handler reads saved mcause, saved mstatus inside trap, notes
//      whether mie auto-cleared. Handler also notes mstatus.mpie
//      (should be 1, saving the pre-trap mie=1 value) and mpp
//      (should be 3 = PRIV_LVL_M).
//   6. Handler advances mepc past the ecall (so we don't loop), mret.
//   7. Main reads mstatus after mret → expect mie restored from mpie
//      (1), mpie restored to 1, mpp restored to U (0).

#include "common.h"

// RISC-V spec bit positions in mstatus
#define MSTATUS_MIE_BIT    3
#define MSTATUS_MPIE_BIT   7
#define MSTATUS_MPP_LOW    11

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

    // ── snapshot mstatus right out of reset ───────────────────
    csrr   t0, mstatus
    la     t1, snap_reset
    sw     t0, 0(t1)

    // ── enable mie via csrs ───────────────────────────────────
    li     t0, (1 << MSTATUS_MIE_BIT)
    csrs   mstatus, t0
    csrr   t0, mstatus
    la     t1, snap_mie_set
    sw     t0, 0(t1)

    // ── take an ecall ──────────────────────────────────────────
    // Cause 11 (M-ecall). Handler below stashes saved_mstatus_in_trap
    // and advances mepc past the ecall.
    ecall

    // control returns here after mret
    // ── snapshot mstatus after mret ────────────────────────────
    csrr   t0, mstatus
    la     t1, snap_after_mret
    sw     t0, 0(t1)

    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    // Stash mcause + mstatus as seen INSIDE the trap.
    csrr   t0, mcause
    la     t1, saved_mcause_in_trap
    sw     t0, 0(t1)

    csrr   t0, mstatus
    la     t1, saved_mstatus_in_trap
    sw     t0, 0(t1)

    // Advance mepc past the ecall (ecall is 4 bytes on RV32I).
    csrr   t0, mepc
    addi   t0, t0, 4
    csrw   mepc, t0

    mret


.section .data
.globl snap_reset
.globl snap_mie_set
.globl saved_mcause_in_trap
.globl saved_mstatus_in_trap
.globl snap_after_mret
.globl done_marker
.align 2
snap_reset:             .word 0
snap_mie_set:           .word 0
saved_mcause_in_trap:   .word 0
saved_mstatus_in_trap:  .word 0
snap_after_mret:        .word 0
done_marker:            .word 0
