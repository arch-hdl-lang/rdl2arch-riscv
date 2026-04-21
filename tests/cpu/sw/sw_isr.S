// Phase-6.3 software-interrupt bring-up program.
//
// Exercises the M-mode software-interrupt path end-to-end:
//
//   _start arms mie.MSIE + mstatus.MIE, then writes `1` to CLINT.msip.
//   Our generated `ClintLogic.msip_out` is a combinational view of
//   `hwif_out.msip_value != 0`, so it goes high on the next cycle and
//   Ibex's `irq_software_i` input sees it → mip.MSIP = 1 → trap, cause 3.
//
//   Handler reads mcause/mepc/mip, stores them, WRITES 0 BACK TO
//   CLINT.msip (so mip.MSIP drops immediately — that's how msip
//   interrupts are acknowledged on CLINT), flags `saw_trap`, mrets.
//
// This is the "software fires its own interrupt" case — the dual of
// timer_isr.S's "hardware fires on a comparator".

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

    // Enable only the M-software interrupt.
    li     t0, MIE_MSIE_MASK
    csrw   mie, t0

    // Unleash traps.
    li     t0, MSTATUS_MIE_MASK
    csrs   mstatus, t0

    // Raise the software interrupt: write 1 to CLINT.msip.
    li     t0, CLINT_MSIP
    li     t1, 1
    sw     t1, 0(t0)

    // Busy-wait for the ISR to flag.
    la     t0, saw_trap
1:  lw     t1, 0(t0)
    beqz   t1, 1b

    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    csrr   t0, mcause
    la     t1, saved_mcause
    sw     t0, 0(t1)

    csrr   t0, mepc
    la     t1, saved_mepc
    sw     t0, 0(t1)

    csrr   t0, mip
    la     t1, saved_mip
    sw     t0, 0(t1)

    // Acknowledge the software interrupt by clearing CLINT.msip.
    // Because mip.MSIP is a combinational mirror of CLINT.msip, this
    // drops the interrupt on the very next cycle — no need to also
    // clear mie.MSIE.
    li     t0, CLINT_MSIP
    sw     zero, 0(t0)

    la     t0, saw_trap
    li     t1, 1
    sw     t1, 0(t0)

    mret


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
