// Phase-6.5e mie / mip swap-in verification.
//
// The 4 interrupt programs (timer/sw/ext/multictx) already prove
// `mie` works end-to-end: each one does a `csrw mie, <bit>` that
// must survive round-trip and gate the controller correctly, or
// the trap never fires. This program adds focused SW-only
// coverage:
//
//   1. Read mie at reset → 0.
//   2. csrrw mie, pattern → readback equals pattern (masked to
//      the modelled bits MSIE/MTIE/MEIE/MFIE[0]; WPRI bits read
//      as zero).
//   3. csrrc / csrrs cycle on mie.
//   4. Read mip → the adapter drives hwif_in.mip_* from live
//      `irq_*_i` every cycle. With no source active,
//      `csrr t0, mip` reads 0.
//   5. Raise a CLINT software interrupt by writing CLINT.msip = 1,
//      WITHOUT enabling mie.MSIE. The ISR path must stay quiet
//      (mie gates the trap), but mip.MSIP should show 1 on the
//      next `csrr`.
//   6. Clear CLINT.msip; mip.MSIP falls back to 0.

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

    // ── mie at reset ──────────────────────────────────────────
    csrr   t0, mie
    la     t1, snap_mie_reset
    sw     t0, 0(t1)

    // ── mie round-trip ────────────────────────────────────────
    // Write MSIE(3) + MTIE(7) + MEIE(11) + MFIE[0](16) all set.
    // Upper bits (WPRI) are discarded by the RDL emit.
    li     t0, (1 << 3) | (1 << 7) | (1 << 11) | (1 << 16)
    csrw   mie, t0
    csrr   t1, mie
    la     t2, snap_mie_all
    sw     t1, 0(t2)

    // ── mie csrrc (clear MTIE) ────────────────────────────────
    li     t0, (1 << 7)
    csrrc  zero, mie, t0
    csrr   t1, mie
    la     t2, snap_mie_mtie_cleared
    sw     t1, 0(t2)

    // ── mie csrrs (set MTIE back) ─────────────────────────────
    li     t0, (1 << 7)
    csrrs  zero, mie, t0
    csrr   t1, mie
    la     t2, snap_mie_mtie_set
    sw     t1, 0(t2)

    // ── mip quiet (no IRQ source active) ──────────────────────
    // mstatus.MIE stays 0 throughout this test so even though mie
    // bits are set, no trap fires while we poke the sources.
    csrr   t0, mip
    la     t1, snap_mip_quiet
    sw     t0, 0(t1)

    // ── raise CLINT.msip → observe mip.MSIP high ──────────────
    li     t0, CLINT_MSIP
    li     t1, 1
    sw     t1, 0(t0)
    // Small delay so the CLINT's combinational msip_out → our
    // CsrFile's 1-cycle-lagged mip storage has settled.
    nop
    nop
    nop
    csrr   t0, mip
    la     t1, snap_mip_msip_raised
    sw     t0, 0(t1)

    // ── clear CLINT.msip → mip.MSIP drops ─────────────────────
    li     t0, CLINT_MSIP
    sw     zero, 0(t0)
    nop
    nop
    nop
    csrr   t0, mip
    la     t1, snap_mip_msip_lowered
    sw     t0, 0(t1)

    la     t0, done_marker
    li     t1, 0xFEEDFACE
    sw     t1, 0(t0)

halt:
    j      halt


.globl common_trap_handler
.align 2
common_trap_handler:
    // mstatus.MIE is off during this whole test, so any trap is a
    // bug (synchronous exception we didn't expect).
    csrr   t0, mcause
    la     t1, unexpected_trap_mcause
    sw     t0, 0(t1)
trap_park:
    j      trap_park


.section .data
.globl snap_mie_reset
.globl snap_mie_all
.globl snap_mie_mtie_cleared
.globl snap_mie_mtie_set
.globl snap_mip_quiet
.globl snap_mip_msip_raised
.globl snap_mip_msip_lowered
.globl done_marker
.globl unexpected_trap_mcause
.align 2
snap_mie_reset:          .word 0
snap_mie_all:            .word 0
snap_mie_mtie_cleared:   .word 0
snap_mie_mtie_set:       .word 0
snap_mip_quiet:          .word 0
snap_mip_msip_raised:    .word 0
snap_mip_msip_lowered:   .word 0
done_marker:             .word 0
unexpected_trap_mcause:  .word 0
