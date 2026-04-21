// Phase-6.2 timer-ISR bring-up program.
//
// What it does
// ------------
//   1. Zero the "saw_trap" flag, stack up.
//   2. mtvec   = trap_entry
//   3. mie.MTIE = 1  (bit 7)
//   4. Read CLINT.mtime_lo, add a small delay, write CLINT.mtimecmp_lo
//      (mtimecmp_hi stays at reset value 0xFFFFFFFF — with a low mtime
//      we'd fire immediately if we cleared it, so we write mtimecmp_hi
//      FIRST to zero, then set mtimecmp_lo with the future deadline).
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
// All symbol addresses MUST match what the cocotb driver expects. See
// `tests/cpu/cocotb_tests/test_timer_isr.py:MEMMAP` for the shared
// contract.

#define CLINT_BASE          0x02000000
#define CLINT_MSIP          (CLINT_BASE + 0x0000)
#define CLINT_MTIMECMP_LO   (CLINT_BASE + 0x4000)
#define CLINT_MTIMECMP_HI   (CLINT_BASE + 0x4004)
#define CLINT_MTIME_LO      (CLINT_BASE + 0xBFF8)
#define CLINT_MTIME_HI      (CLINT_BASE + 0xBFFC)

// bits
#define MIE_MTIE_MASK       (1 << 7)
#define MIE_MSIE_MASK       (1 << 3)
#define MIE_MEIE_MASK       (1 << 11)
#define MSTATUS_MIE         (1 << 3)

// ── trap vector table ─────────────────────────────────────────────
// Ibex forces `mtvec.MODE = 01` (vectored) in hardware — it masks the
// low two bits of whatever SW writes to mtvec and replaces them with
// `01`. In vectored mode, exceptions go to `mtvec_base` and interrupts
// go to `mtvec_base + 4*cause`. So the first 32 words of `.text.trap`
// must be a jump table, with each 4-byte slot jumping to a common
// handler.
//
// Cause numbers we care about on this SoC:
//   0        exception fallback
//   3        M-software interrupt  (CLINT.msip → mip.MSIP)
//   7        M-timer interrupt     (CLINT.mtip → mip.MTIP)
//   11       M-external interrupt  (PLIC.intr_out[0] → mip.MEIP)
.section .text.trap, "ax"
.globl trap_entry
.align 2
trap_entry:
    j      common_trap_handler      // cause  0 / exception
    j      common_trap_handler      // cause  1
    j      common_trap_handler      // cause  2
    j      common_trap_handler      // cause  3 / msip
    j      common_trap_handler      // cause  4
    j      common_trap_handler      // cause  5
    j      common_trap_handler      // cause  6
    j      common_trap_handler      // cause  7 / mtip
    j      common_trap_handler      // cause  8
    j      common_trap_handler      // cause  9
    j      common_trap_handler      // cause 10
    j      common_trap_handler      // cause 11 / meip
    // Remaining 20 slots (causes 12..31) — same handler, since we
    // want to learn about any stray cause during bring-up rather than
    // jump into the weeds. Exactly 32 slots * 4 bytes = 128 bytes,
    // so the vector table fills `.text.trap` up to 0x0010_0080 where
    // the reset trampoline starts (see link.ld).
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler
    j      common_trap_handler

// Common handler lives in .text (past _start) so it doesn't bleed over
// into `_reset` at 0x0010_0080.
.section .text, "ax"
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

// ── reset trampoline ───────────────────────────────────────────────
// Ibex starts at boot_addr_i + 0x80 = 0x0010_0080.
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
    // base of the 32-entry vector table defined at the top of this
    // file, so the hardware's 4*cause offset lands on a well-formed
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
    li     t0, MSTATUS_MIE
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
