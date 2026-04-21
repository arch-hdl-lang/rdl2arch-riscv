// Shared `#define`s for the cpu/ bring-up programs.
//
// GCC preprocesses `.S` files (uppercase), so these #defines are
// available directly in the assembly.

#ifndef RDL2ARCH_RISCV_CPU_TESTS_COMMON_H
#define RDL2ARCH_RISCV_CPU_TESTS_COMMON_H

// ── memory map (mirrors ibex_mini_soc.sv) ───────────────────────────
#define RAM_BASE                0x00100000

#define CLINT_BASE              0x02000000
#define CLINT_MSIP              (CLINT_BASE + 0x0000)
#define CLINT_MTIMECMP_LO       (CLINT_BASE + 0x4000)
#define CLINT_MTIMECMP_HI       (CLINT_BASE + 0x4004)
#define CLINT_MTIME_LO          (CLINT_BASE + 0xBFF8)
#define CLINT_MTIME_HI          (CLINT_BASE + 0xBFFC)

#define PLIC_BASE               0x0C000000
#define PLIC_PRIORITY_BASE      (PLIC_BASE + 0x0000)
#define PLIC_PENDING            (PLIC_BASE + 0x1000)
#define PLIC_ENABLE_0           (PLIC_BASE + 0x2000)
#define PLIC_THRESHOLD_0        (PLIC_BASE + 0x200000)
#define PLIC_CLAIM_0            (PLIC_BASE + 0x200004)

// ── mie / mstatus bit masks (RISC-V privileged spec encoding) ───────
#define MIE_MSIE_MASK           (1 << 3)
#define MIE_MTIE_MASK           (1 << 7)
#define MIE_MEIE_MASK           (1 << 11)
#define MSTATUS_MIE_MASK        (1 << 3)

#endif // RDL2ARCH_RISCV_CPU_TESTS_COMMON_H
