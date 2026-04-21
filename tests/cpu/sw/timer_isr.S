// Phase-6.2 timer-ISR bring-up program.
//
// What it does
// ------------
//   1. Zero the "saw_trap" flag, stack up.
//   2. mtvec   = trap_entry
//   3. mie.MTIE = 1  (bit 7)
//   4. Read CLINT.mtime_lo, add a small delay, write CLINT.mtimecmp_lo.
//   5. mstatus.MIE = 1  (bit 3) — unleash traps.
//   6. Busy-wait on the in-RAM `saw_trap` byte.
//   7. On trap: read mcause/mepc/mip into saved_* slots, clear mie.MTIE
//      (so we don't re-fire), set saw_trap=1, mret.
//   8. Main loop sees saw_trap set, writes `done_marker = 0xFEEDFACE`
//      to an agreed RAM address, and halts (j .).
//
// The cocotb testbench polls `done_marker` and the `saved_*` slots
// through a hierarchical ref into the RAM, asserting correct values.
//
// All symbol addresses MUST match what the cocotb driver expects —
// see `tests/cpu/cocotb_tests/test_timer_isr.py:MEMMAP`.

#include "common.h"

// ── trap vector table + reset trampoline (shared layout) ────────────
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

// ── main ───────────────────────────────────────────────────────────
.section .text, "ax"
.globl _start
.align 2
_start:
    // Stack set up. No `gp` — we don't use the small-data addressing
    // mode, and setting up `__global_pointer$` would require extra
    // PROVIDE glue in the linker script.
    la     sp, _stack_top

    // mtvec — Ibex forces mode=01 (vectored). `trap_entry` is the
    // base of the 32-entry vector table at the top of this file, so
    // the hardware's 4*cause offset lands on a well-formed
    // `j common_trap_handler`.
    la     t0, trap_entry
    csrw   mtvec, t0

    // Enable mie.MTIE only — MSIE / MEIE left masked for this test.
    li     t0, MIE_MTIE_MASK
    csrw   mie, t0

    // Clear mtimecmp_hi first so the eventual comparison isn't
    // dominated by the 0xFFFF_FFFF reset value.
    li     t0, CLINT_MTIMECMP_HI
    sw     zero, 0(t0)

    // Read mtime_lo, program mtimecmp_lo = mtime_lo + 100.
    li     t0, CLINT_MTIME_LO
    lw     t1, 0(t0)
    addi   t1, t1, 100
    li     t0, CLINT_MTIMECMP_LO
    sw     t1, 0(t0)

    // Unleash traps.
    li     t0, MSTATUS_MIE_MASK
    csrs   mstatus, t0

    // Busy-wait.
    la     t0, saw_trap
1:  lw     t1, 0(t0)
    beqz   t1, 1b

    // Done — write the agreed completion sentinel for cocotb.
    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    // Caller-saved scratch: t0-t2 are ours. We clobber only them.
    csrr   t0, mcause
    la     t1, saved_mcause
    sw     t0, 0(t1)

    csrr   t0, mepc
    la     t1, saved_mepc
    sw     t0, 0(t1)

    csrr   t0, mip
    la     t1, saved_mip
    sw     t0, 0(t1)

    // Disarm the timer so we don't re-enter: clear mie.MTIE. Even if
    // mip.MTIP stays high, the core won't trap with mie cleared.
    li     t0, MIE_MTIE_MASK
    csrc   mie, t0

    // Signal main that the ISR ran.
    la     t0, saw_trap
    li     t1, 1
    sw     t1, 0(t0)

    mret


// ── data: cocotb-readable slots ────────────────────────────────────
.section .data
.globl saw_trap
.globl saved_mcause
.globl saved_mepc
.globl saved_mip
.globl done_marker
.align 2
saw_trap:        .word 0
saved_mcause:    .word 0
saved_mepc:      .word 0
saved_mip:       .word 0
done_marker:     .word 0
