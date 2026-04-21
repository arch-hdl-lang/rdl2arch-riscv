"""SystemRDL User-Defined Properties for RISC-V privileged CSRs.

Each UDP tags RDL regs / fields with RISC-V-specific semantics that the
generator consumes:

- riscv_csr_addr       — 12-bit CSR address, overriding RDL's byte address.
- riscv_priv           — minimum privilege for access ("m" / "s" / "u").
                         Also accepted on addrmap / regfile as a `default`.
- riscv_wpri           — reserved bits: reads zero, writes preserved.
- riscv_warl           — write-any-read-legal legalization (bitmask or enum list).
- riscv_trap_signal    — one-cycle pulse output when the tagged field is written.
- riscv_save_on_trap   — auto-written by trap coordinator on trap entry.
- riscv_restore_on_ret — auto-restored by trap coordinator on xRET.
- riscv_intr_clint_role — tags CLINT MMIO regs (msip / mtimecmp_* / mtime_*).
- riscv_intr_plic_role  — tags PLIC MMIO regs (priority / pending / enable /
                          threshold / claim).

The upstream rdl2arch UDPs (``emit_read_pulse`` / ``emit_write_pulse``)
are re-exported here so callers only register a single ``ALL_UDPS``
list. The PLIC generator consumes both pulses on the per-context claim
register to drive spec-compliant claim / complete semantics.
"""

from rdl2arch.udps import ALL_UDPS as _RDL2ARCH_UDPS

from .csr_addr import RiscvCsrAddr
from .privilege import RiscvPriv
from .warl import RiscvWarl
from .wpri import RiscvWpri
from .trap_signal import RiscvTrapSignal
from .trap_lifecycle import RiscvSaveOnTrap, RiscvRestoreOnRet
from .clint import RiscvIntrClintRole
from .plic import RiscvIntrPlicRole

_RISCV_UDPS = [
    RiscvCsrAddr,
    RiscvPriv,
    RiscvWpri,
    RiscvWarl,
    RiscvTrapSignal,
    RiscvSaveOnTrap,
    RiscvRestoreOnRet,
    RiscvIntrClintRole,
    RiscvIntrPlicRole,
]

# Expose upstream rdl2arch UDPs alongside the RISC-V-specific ones so a
# single `register_udp` loop over `ALL_UDPS` covers everything the PLIC
# and CSR fixtures need.
ALL_UDPS = _RISCV_UDPS + list(_RDL2ARCH_UDPS)

__all__ = ["ALL_UDPS"] + [c.__name__ for c in _RISCV_UDPS]
