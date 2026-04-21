"""Phase-6.5b end-to-end mtvec swap-in validation.

After Phase 6.5b, upstream Ibex's `u_mtvec_csr` ibex_csr storage is
*gone* from the hybrid cs_registers; mtvec (CSR 0x305) is now backed
entirely by our generated `MTrapIbexCsrFile.mtvec_*_r` fields. Two
consumers of that storage are tested here:

  1. RISC-V side — `mscratch_csrfile`-style csrw/csrr round-trips,
     verified through readback values stashed to RAM.

  2. `csr_mtvec_o` export — the top-level output from the hybrid
     cs_registers (consumed by Ibex's if_stage for trap-PC calc) is
     now driven by `{hwif_out.mtvec_base, hwif_out.mtvec_mode}`. The
     4 interrupt-driven cpu programs (timer / sw / ext / multictx)
     already exercise the full trap-PC path under this wiring; this
     test only has to prove the CSR read/write semantics are intact.

  3. Init-pulse replay — Ibex's post-reset `csr_mtvec_init_i` pulse
     is intercepted in the adapter and translated into a bus WRITE
     to our CsrFile with `{boot_addr_i[31:8], 6'b0, 2'b01}`. For
     boot_addr = 0x0010_0000 this lands mtvec = 0x0010_0001. The
     first csrr before any SW write must see that value.
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                 = 0x0010_0000
SAVED_READBACK_INIT      = 0x0010_1000
SAVED_READBACK_A         = 0x0010_1004
SAVED_READBACK_B         = 0x0010_1008
DONE_MARKER              = 0x0010_100C
UNEXPECTED_TRAP_MCAUSE   = 0x0010_1010


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
async def mtvec_rw_routes_through_generated_csrfile(dut) -> None:
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
        rb_init = _mem_word(dut, SAVED_READBACK_INIT)
        a       = _mem_word(dut, SAVED_READBACK_A)
        b       = _mem_word(dut, SAVED_READBACK_B)
        mcause  = _mem_word(dut, UNEXPECTED_TRAP_MCAUSE)
        raise AssertionError(
            f"mtvec program never completed; "
            f"saved_readback_init={rb_init:#x} saved_readback_a={a:#x} "
            f"saved_readback_b={b:#x} unexpected_mcause={mcause:#x}"
        )

    assert _mem_word(dut, UNEXPECTED_TRAP_MCAUSE) == 0, (
        "program hit the unexpected-trap path — "
        f"mcause={_mem_word(dut, UNEXPECTED_TRAP_MCAUSE):#x}"
    )

    # Init-pulse replay lands 0x0010_0001 in mtvec before _start runs.
    assert _mem_word(dut, SAVED_READBACK_INIT) == 0x0010_0001, (
        f"post-reset mtvec readback expected 0x00100001 (init-pulse "
        f"value {{boot_addr[31:8], 6'b0, 2'b01}}), got "
        f"{_mem_word(dut, SAVED_READBACK_INIT):#x}"
    )

    # Round A — aligned pattern, mode = 00.
    assert _mem_word(dut, SAVED_READBACK_A) == 0xA5A5A5A4, (
        f"round A readback: got {_mem_word(dut, SAVED_READBACK_A):#x}, "
        f"expected 0xA5A5A5A4"
    )

    # Round B — base changed, mode = 01 (vectored). Our WARL accepts.
    assert _mem_word(dut, SAVED_READBACK_B) == 0x12345671, (
        f"round B readback: got {_mem_word(dut, SAVED_READBACK_B):#x}, "
        f"expected 0x12345671"
    )

    # Direct hierarchical peek into the generated CsrFile. After the
    # final `csrw mtvec, trap_entry` in the program, storage should
    # equal {trap_entry[31:2], trap_entry[1:0]}.
    #
    # The SV packed struct for `MtvecReg` is
    #   struct packed { logic [1:0] mode; logic [29:0] base; };
    # First-declared field is MSB, so reading the whole struct gives
    # `(mode << 30) | base`, *not* the RISC-V spec bit layout
    # (bits[31:2]=base, bits[1:0]=mode). The CsrFile's rdata mux
    # concats `{base, mode}` explicitly to produce the spec-ordered
    # 32-bit value; we undo the struct packing here the same way.
    # cocotb surfaces packed structs as a single LogicArrayObject —
    # no per-field handles on a fully-packed type — so we read the
    # whole word and decode manually.
    mtvec_r = (dut.u_ibex.u_ibex_top.u_ibex_core
                  .cs_registers_i
                  .u_ourfile
                  .mtvec_r)
    packed = int(mtvec_r.value)
    mode = (packed >> 30) & 0x3
    base = packed & ((1 << 30) - 1)
    ourfile_mtvec = (base << 2) | mode
    TRAP_ENTRY = 0x0010_0000     # matches link.ld / common.h layout
    assert ourfile_mtvec == TRAP_ENTRY, (
        f"CsrFile's mtvec_r = {{base={base:#x}, mode={mode}}} → "
        f"{ourfile_mtvec:#x}, expected {TRAP_ENTRY:#x} "
        f"(the final SW-written trap_entry address)"
    )

    # Core-side output wire should also reflect the stored value.
    csr_mtvec_o_lane = int(
        dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.csr_mtvec_o.value
    )
    assert csr_mtvec_o_lane == TRAP_ENTRY, (
        f"csr_mtvec_o = {csr_mtvec_o_lane:#x}, expected {TRAP_ENTRY:#x}"
    )
