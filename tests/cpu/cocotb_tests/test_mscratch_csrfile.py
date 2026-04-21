"""Phase-6.5a end-to-end mscratch swap-in validation.

Our forked `ibex_cs_registers_hybrid.sv` removed Ibex's internal
`u_mscratch_csr` instance entirely — mscratch storage now lives inside
the generated `MTrapMscratchCsrFile`. This test proves the swap is
live by:

  1. Running a tiny RV32 program that writes / reads mscratch twice
     (Program `mscratch_csrfile.S`).
  2. Asserting the read-back values match the writes (proves storage
     is being written and later read correctly).
  3. Peeking the backing `mscratch_r.value` register *inside the
     generated CsrFile* via hierarchical access — confirms the value
     we wrote via RISC-V ended up in the CsrFile's state, not in any
     other Ibex-internal store.

With Ibex's own `u_mscratch_csr` removed, correct readbacks alone are
already enough to prove the path; the hierarchical peek is a
belt-and-suspenders cross-check that the cycle Phase-6.5b+ reworks
haven't regressed.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                = 0x0010_0000
SAVED_READBACK_A        = 0x0010_1000
SAVED_READBACK_B        = 0x0010_1004
DONE_MARKER             = 0x0010_1008
UNEXPECTED_TRAP_MCAUSE  = 0x0010_100C


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
                raise RuntimeError(
                    f"unexpected @addr line in {path}: {line!r}"
                )
            dut.u_ram.u_ram.mem[count].value = int(line, 16)
            count += 1
    return count


@cocotb.test()
async def mscratch_rw_routes_through_generated_csrfile(dut) -> None:
    cocotb.start_soon(Clock(dut.IO_CLK, 10, units="ns").start())
    dut.ext_irq_sources_i.value = 0

    # Clean 1→0 async-reset edge for Ibex.
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
        mcause = _mem_word(dut, UNEXPECTED_TRAP_MCAUSE)
        a      = _mem_word(dut, SAVED_READBACK_A)
        b      = _mem_word(dut, SAVED_READBACK_B)
        raise AssertionError(
            f"mscratch program never completed; "
            f"saved_readback_a={a:#x} saved_readback_b={b:#x} "
            f"unexpected_mcause={mcause:#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — mcause="
        f"{_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # Round-trip A.
    assert _mem_word(dut, SAVED_READBACK_A) == 0xC0FFEE42, (
        f"round A readback mismatch: "
        f"got {_mem_word(dut, SAVED_READBACK_A):#x}, expected 0xC0FFEE42"
    )
    # Round-trip B.
    assert _mem_word(dut, SAVED_READBACK_B) == 0xDEADBEEF, (
        f"round B readback mismatch: "
        f"got {_mem_word(dut, SAVED_READBACK_B):#x}, expected 0xDEADBEEF"
    )

    # Direct peek into our CsrFile's storage — confirms the swap is
    # live. The hierarchical path points into the generated file
    # instanced by our forked cs_registers.
    # `MscratchReg` is a packed struct with a single 32-bit `value`
    # field, so reading the whole register gives us the mscratch
    # word directly (cocotb coerces the packed bit vector).
    mscratch_r = (dut.u_ibex.u_ibex_top.u_ibex_core
                     .cs_registers_i
                     .u_ourfile
                     .mscratch_r)
    ourfile_mscratch = int(mscratch_r.value)
    assert ourfile_mscratch == 0xDEADBEEF, (
        f"generated CsrFile's mscratch_r.value = {ourfile_mscratch:#x}, "
        f"expected 0xDEADBEEF (the last write) — is the swap live?"
    )
