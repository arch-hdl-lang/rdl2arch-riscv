"""Phase-6.5d end-to-end mstatus swap-in validation.

Confirms that the full mstatus state machine — SW writes,
trap-entry auto-clear of `mie`, trap-entry save of `mpie` and `mpp`,
and mret restore of all three — operates through our generated
`MTrapIbexCsrFile`. The 4 interrupt-driven cpu programs (timer / sw
/ ext / multictx) already exercise this path implicitly; this one
just makes the assertions explicit and adds a synchronous-exception
(ecall) path that the others don't hit.

Timeline observed by the program:

   reset                          : mstatus.mie = 0   (RDL default)
   csrs mstatus, 1<<3             : mstatus.mie = 1
   `ecall`                        : trap to cause 11
   trap-entry HW drives           : mie ← 0, mpie ← 1 (old mie),
                                     mpp ← PRIV_LVL_M (3)
   handler reads mstatus          : mie=0, mpie=1, mpp=3 observable
   handler advances mepc, mret    :
   mret HW drives                 : mie ← mpie (=1), mpie ← 1,
                                     mpp ← PRIV_LVL_U (0)
   main reads mstatus after mret  : mie=1, mpie=1, mpp=0
"""

from __future__ import annotations

import os

import cocotb
from cocotb.clock import Clock
from cocotb.triggers import RisingEdge


RAM_BASE                 = 0x0010_0000
SNAP_RESET               = 0x0010_1000
SNAP_MIE_SET             = 0x0010_1004
SAVED_MCAUSE_IN_TRAP     = 0x0010_1008
SAVED_MSTATUS_IN_TRAP    = 0x0010_100C
SNAP_AFTER_MRET          = 0x0010_1010
DONE_MARKER              = 0x0010_1014


# Spec bit positions
MIE_BIT   = 3
MPIE_BIT  = 7
MPP_LOW   = 11

# Priv-level encoding (priv_lvl_e in ibex_pkg)
PRIV_LVL_M = 0b11
PRIV_LVL_U = 0b00


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


def _mstatus_fields(word: int) -> dict:
    return {
        "mie":  (word >> MIE_BIT)  & 1,
        "mpie": (word >> MPIE_BIT) & 1,
        "mpp":  (word >> MPP_LOW)  & 0b11,
    }


@cocotb.test()
async def mstatus_state_machine_routes_through_csrfile(dut) -> None:
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
            "mstatus program never completed. "
            f"snap_reset={_mem_word(dut, SNAP_RESET):#x} "
            f"snap_mie_set={_mem_word(dut, SNAP_MIE_SET):#x} "
            f"mcause_in_trap={_mem_word(dut, SAVED_MCAUSE_IN_TRAP):#x} "
            f"mstatus_in_trap={_mem_word(dut, SAVED_MSTATUS_IN_TRAP):#x} "
            f"snap_after_mret={_mem_word(dut, SNAP_AFTER_MRET):#x}"
        )

    # 1. Right out of reset: mie=0, mpie=0 (our RDL default — differs from
    #    upstream Ibex's mpie=1 reset, but nothing observes that before a
    #    trap fires).
    reset_f = _mstatus_fields(_mem_word(dut, SNAP_RESET))
    assert reset_f["mie"] == 0, f"reset mstatus.mie should be 0: {reset_f}"

    # 2. After `csrs mstatus, 1<<3`: mie set to 1.
    set_f = _mstatus_fields(_mem_word(dut, SNAP_MIE_SET))
    assert set_f["mie"] == 1, f"post-csrs mie should be 1: {set_f}"

    # 3. Inside the trap (cause 11 / M-ecall):
    mcause = _mem_word(dut, SAVED_MCAUSE_IN_TRAP)
    assert mcause == 11, (
        f"trap mcause should be 11 (M-ecall), got {mcause:#x}"
    )
    trap_f = _mstatus_fields(_mem_word(dut, SAVED_MSTATUS_IN_TRAP))
    # trap-entry must have auto-cleared mie:
    assert trap_f["mie"] == 0, (
        f"mie should be auto-cleared on trap entry, got {trap_f}"
    )
    # trap-entry save of the previous mie into mpie (was 1):
    assert trap_f["mpie"] == 1, (
        f"mpie should hold pre-trap mie (1), got {trap_f}"
    )
    # trap-entry save of the previous priv level into mpp (M = 3):
    assert trap_f["mpp"] == PRIV_LVL_M, (
        f"mpp should be priv_lvl_q (M=3) at trap entry, got {trap_f}"
    )

    # 4. After mret:
    post_f = _mstatus_fields(_mem_word(dut, SNAP_AFTER_MRET))
    # mie restored from mpie (was 1):
    assert post_f["mie"] == 1, (
        f"mret should restore mie from mpie (=1), got {post_f}"
    )
    # mpie set to 1 (spec default on mret):
    assert post_f["mpie"] == 1, (
        f"mret should set mpie to 1, got {post_f}"
    )
    # mpp set to U (spec default on mret):
    assert post_f["mpp"] == PRIV_LVL_U, (
        f"mret should set mpp to U (0), got {post_f}"
    )

    # 5. Direct hierarchical peek into CsrFile storage at end of program.
    cs = dut.u_ibex.u_ibex_top.u_ibex_core.cs_registers_i.u_ourfile
    mstatus_r = cs.mstatus_r
    # Packed struct layout (MSB → LSB):
    #   wpri_lo(1) | reserved_2_1(2) | mie(1) | reserved_6_4(3) |
    #   mpie(1)    | reserved_10_8(3)| mpp(2) | wpri_hi(19)
    # → mie is at packed bit 28, mpie at 24, mpp at 21..20.
    packed = int(mstatus_r.value)
    file_mie  = (packed >> 28) & 1
    file_mpie = (packed >> 24) & 1
    file_mpp  = (packed >> 20) & 0b11
    assert file_mie == 1, f"CsrFile mstatus_r.mie = {file_mie}, expected 1"
    assert file_mpie == 1, (
        f"CsrFile mstatus_r.mpie = {file_mpie}, expected 1"
    )
    assert file_mpp == PRIV_LVL_U, (
        f"CsrFile mstatus_r.mpp = {file_mpp}, expected 0 (U)"
    )
