"""Phase-6.7 end-to-end debug CSR swap-in validation.

Confirms `dcsr` / `dpc` / `dscratch0` / `dscratch1` storage has moved
from Ibex's four `ibex_csr` instances into our generated
`MTrapIbexCsrFile`.

What this actually verifies:
  * The hybrid (adapter + generated CsrFile) lints and builds
    cleanly with the four `u_{dcsr, depc, dscratch0, dscratch1}_csr`
    instances removed from upstream.
  * Every pre-6.7 cpu program still runs end-to-end through the
    rebuilt hybrid (covered by the 10 other `test_cpu_programs.py`
    cases — if any of them regressed, you'd see it first).
  * The adapter's rewired references — `csr_depc_o`,
    `debug_{single_step, ebreakm, ebreaku}_o`, and the
    `priv_lvl_d <= priv_lvl_e'(dcsr_prv)` dret restore — all resolve
    against our CsrFile's `hwif_out` correctly (no port-missing
    errors at elaboration).
  * A trivial run-to-completion sanity program finishes without
    taking an unexpected trap, proving the CsrFile's presence
    doesn't break the program's control flow.

Debug CSRs are architecturally inaccessible from M-mode (Ibex raises
"illegal CSR" when `debug_mode_i = 0`), and our `DbgTriggerEn=0` SoC
never enters debug mode — so there's no csrr/csrw exercise path from
a regular cpu program. The richer coverage comes from the pre-6.7
programs continuing to work + lint-level type agreement.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                = 0x0010_0000
DONE_MARKER             = 0x0010_1000
UNEXPECTED_TRAP_MCAUSE  = 0x0010_1004


def _mem_word(dut, byte_addr: int) -> int:
    idx = (byte_addr - RAM_BASE) // 4
    return int(dut.u_ram.u_ram.mem[idx].value)


def _load_vmem(dut, path: str) -> int:
    with open(path) as fh:
        count = 0
        for line in fh:
            line = line.strip()
            if not line or line.startswith("//") or line.startswith("#"):
                continue
            if line.startswith("@"):
                raise RuntimeError(f"unexpected @addr in {path}: {line!r}")
            dut.u_ram.u_ram.mem[count].value = int(line, 16)
            count += 1
    return count


@cocotb.test()
async def debug_csrs_sanity(dut) -> None:
    cocotb.start_soon(Clock(dut.IO_CLK, 10, units="ns").start())
    dut.ext_irq_sources_i.value = 0

    dut.IO_RST_N.value = 1
    await RisingEdge(dut.IO_CLK)
    dut.IO_RST_N.value = 0
    await RisingEdge(dut.IO_CLK)

    vmem_path = os.environ["VMEM_PATH"]
    loaded = _load_vmem(dut, vmem_path)
    dut._log.info(f"loaded {loaded} words from {vmem_path}")

    for _ in range(5):
        await RisingEdge(dut.IO_CLK)
    dut.IO_RST_N.value = 1

    for _ in range(2000):
        await RisingEdge(dut.IO_CLK)
        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        raise AssertionError(
            "debug CSR sanity program never completed. "
            f"unexpected_trap_mcause="
            f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # Hierarchical peek: the four registers exist inside u_ourfile.
    # If the 6.7 migration didn't happen, these signals wouldn't
    # resolve and the access would raise AttributeError — catching
    # a regression where someone accidentally backs them out of
    # the RDL fixture.
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile
    _ = int(cs.dcsr_r.value)
    _ = int(cs.dpc_r.value)
    _ = int(cs.dscratch0_r.value)
    _ = int(cs.dscratch1_r.value)
