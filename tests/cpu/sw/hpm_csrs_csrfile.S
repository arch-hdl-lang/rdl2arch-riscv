// Phase-6.8 HPM counter swap-in verification.
//
// Exercises the SW-visible parts of the migrated block:
//   * `mhpmevent3..12`  — RO constants, reads `1 << (N - 3)`;
//                         writes silently dropped.
//   * `mhpmcounter3..12` + `mhpmcounter3h..12h` — SW-writable (per
//                         spec) with HW auto-increment on the
//                         per-counter enable. We freeze the
//                         counters via `mcountinhibit.hpm[12:3]`
//                         first so writes stick deterministically.
//
// Sequence:
//   1. Freeze all HPM counters by setting mcountinhibit bits 3..12.
//   2. Snapshot mhpmevent3, mhpmevent5, mhpmevent12.
//   3. csrw mhpmevent3, 0xFFFFFFFF → mhpmevent3 still reads 0x1.
//   4. csrw mhpmcounter3, 0xDEADBEEF → readback.
//   5. csrw mhpmcounter12, 0x12345678 → readback.
//   6. csrw mhpmcounter5h, 0xCAFEBABE → readback.

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

    // 1. Freeze HPM counters 3..12: mcountinhibit |= 0x1FF8.
    li     t0, 0x1FF8
    csrrs  zero, 0x320, t0

    // 2. mhpmevent snapshots.
    csrr   t0, 0x323              // mhpmevent3
    la     t1, snap_event3
    sw     t0, 0(t1)
    csrr   t0, 0x325              // mhpmevent5
    la     t1, snap_event5
    sw     t0, 0(t1)
    csrr   t0, 0x32C              // mhpmevent12
    la     t1, snap_event12
    sw     t0, 0(t1)

    // 3. csrw mhpmevent3, 0xFFFFFFFF → readback should stay 0x1.
    li     t0, 0xFFFFFFFF
    csrw   0x323, t0
    csrr   t0, 0x323
    la     t1, snap_event3_after_write
    sw     t0, 0(t1)

    // 4. csrw mhpmcounter3, 0xDEADBEEF → readback.
    li     t0, 0xDEADBEEF
    csrw   0xB03, t0
    csrr   t0, 0xB03
    la     t1, snap_counter3_low
    sw     t0, 0(t1)

    // 5. csrw mhpmcounter12, 0x12345678 → readback.
    li     t0, 0x12345678
    csrw   0xB0C, t0
    csrr   t0, 0xB0C
    la     t1, snap_counter12_low
    sw     t0, 0(t1)

    // 6. csrw mhpmcounter5h, 0xCAFEBABE → readback.
    li     t0, 0xCAFEBABE
    csrw   0xB85, t0
    csrr   t0, 0xB85
    la     t1, snap_counter5_high
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
.globl snap_event3
.globl snap_event5
.globl snap_event12
.globl snap_event3_after_write
.globl snap_counter3_low
.globl snap_counter12_low
.globl snap_counter5_high
.globl done_marker
.globl unexpected_trap_mcause
.align 2
snap_event3:              .word 0
snap_event5:              .word 0
snap_event12:             .word 0
snap_event3_after_write:  .word 0
snap_counter3_low:        .word 0
snap_counter12_low:       .word 0
snap_counter5_high:       .word 0
done_marker:              .word 0
unexpected_trap_mcause:   .word 0
