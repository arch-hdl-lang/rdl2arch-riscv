"""Phase-6.8 end-to-end HPM counter swap-in validation.

Confirms the migration of 30 HPM registers onto our CsrFile:
  * `mhpmevent3..12`  — RO constants, reset to `1 << (N - 3)`;
                        writes are silently dropped (sw=r).
  * `mhpmcounter3..12` + `mhpmcounter3h..12h` — SW-writable, HW
                        auto-increments on per-counter
                        `hpmN_inc_en` (= `mhpmcounter_incr[N] &
                        ~mcountinhibit[N]`). With the counters
                        inhibited via `mcountinhibit.hpm`, SW
                        writes should stick exactly.

Pre-6.8 cpu programs continue to pass end-to-end, proving the
adapter rewires (30 read arms + 30 write arms + per-counter enable
wiring + per-counter `mhpmcounter[Cnt]` RVFI-shim drives) haven't
regressed anything.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                   = 0x0010_0000
SNAP_EVENT3                = 0x0010_1000
SNAP_EVENT5                = 0x0010_1004
SNAP_EVENT12               = 0x0010_1008
SNAP_EVENT3_AFTER_WRITE    = 0x0010_100C
SNAP_COUNTER3_LOW          = 0x0010_1010
SNAP_COUNTER12_LOW         = 0x0010_1014
SNAP_COUNTER5_HIGH         = 0x0010_1018
DONE_MARKER                = 0x0010_101C
UNEXPECTED_TRAP_MCAUSE     = 0x0010_1020


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
async def hpm_csrs_route_through_csrfile(dut) -> None:
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

    for _ in range(3000):
        await RisingEdge(dut.IO_CLK)
        if _mem_word(dut, DONE_MARKER) == 0xFEEDFACE:
            break
    else:
        raise AssertionError(
            "HPM CSR program never completed. "
            f"snap_event3={_mem_word(dut, SNAP_EVENT3):#x} "
            f"unexpected_trap_mcause="
            f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # mhpmevent readouts match the hardwired `1 << (N - 3)` encoding.
    assert _mem_word(dut, SNAP_EVENT3)  == 0x1,   (
        f"mhpmevent3 should read 0x1, got {_mem_word(dut, SNAP_EVENT3):#x}"
    )
    assert _mem_word(dut, SNAP_EVENT5)  == 0x4,   (
        f"mhpmevent5 should read 0x4, got {_mem_word(dut, SNAP_EVENT5):#x}"
    )
    assert _mem_word(dut, SNAP_EVENT12) == 0x200, (
        f"mhpmevent12 should read 0x200, got {_mem_word(dut, SNAP_EVENT12):#x}"
    )

    # csrw mhpmevent3, 0xFFFFFFFF → writes discarded (sw=r), still 0x1.
    assert _mem_word(dut, SNAP_EVENT3_AFTER_WRITE) == 0x1, (
        f"mhpmevent3 should stay 0x1 after csrw (sw=r), got "
        f"{_mem_word(dut, SNAP_EVENT3_AFTER_WRITE):#x}"
    )

    # mhpmcounter round-trips (with counters frozen).
    assert _mem_word(dut, SNAP_COUNTER3_LOW)   == 0xDEADBEEF
    assert _mem_word(dut, SNAP_COUNTER12_LOW)  == 0x12345678
    assert _mem_word(dut, SNAP_COUNTER5_HIGH)  == 0xCAFEBABE

    # Direct hierarchical peeks into CsrFile storage.
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile
    assert int(cs.mhpmcounter3_r.value)  == 0xDEADBEEF
    assert int(cs.mhpmcounter12_r.value) == 0x12345678
    assert int(cs.mhpmcounter5h_r.value) == 0xCAFEBABE
