// Phase-6.5b mtvec swap-in verification.
//
// The 4 existing interrupt-driven programs (timer/sw/ext/multictx)
// already exercise mtvec end-to-end: each `_start` does
// `la t0, trap_entry; csrw mtvec, t0`, then relies on Ibex's
// if_stage reading `csr_mtvec_o` to compute the trap PC. Under
// Phase 6.5b that write goes through our generated CsrFile, and
// the core-side output is driven from the file's hwif_out. Those
// tests passing is the real integration proof.
//
// This program is a focused unit-style check that complements
// them: push two distinct patterns through the CSR and verify the
// readback. Also exercises Ibex's forced init-replay — at reset
// our adapter writes `{boot_addr[31:8], 6'b0, 2'b01}` into mtvec
// before any SW instruction runs, so the very first readback
// should see that init value, not the RDL reset (0).

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

    // ── round A: read what the init-pulse deposited ───────────
    // Our adapter replays `csr_mtvec_init_i` as a bus WRITE to our
    // CsrFile with `{boot_addr_i[31:8], 6'b0, 2'b01}`. boot_addr is
    // 0x0010_0000, so the expected post-reset value is 0x0010_0001.
    csrr   t0, mtvec
    la     t1, saved_readback_init
    sw     t0, 0(t1)

    // ── round B: SW write + readback (aligned — mode bits zero) ─
    li     t0, 0xA5A5A5A4                // base [31:2] + mode 00
    csrw   mtvec, t0
    csrr   t0, mtvec
    la     t1, saved_readback_a
    sw     t0, 0(t1)

    // ── round C: SW write with mode=01 (vectored) ─────────────
    // WARL accepts both 0 and 1 for mode; value survives round-trip.
    li     t0, 0x12345671                // base + mode 01
    csrw   mtvec, t0
    csrr   t0, mtvec
    la     t1, saved_readback_b
    sw     t0, 0(t1)

    // ── restore mtvec so a stray trap still lands sanely ──────
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
    // No interrupts are enabled; any trap is a bug.
    csrr   t0, mcause
    la     t1, unexpected_trap_mcause
    sw     t0, 0(t1)
trap_park:
    j      trap_park


.section .data
.globl saved_readback_init
.globl saved_readback_a
.globl saved_readback_b
.globl done_marker
.globl unexpected_trap_mcause
.align 2
saved_readback_init:     .word 0
saved_readback_a:        .word 0
saved_readback_b:        .word 0
done_marker:             .word 0
unexpected_trap_mcause:  .word 0
