"""SystemRDL User-Defined Properties for RISC-V privileged CSRs.

Each UDP tags RDL regs / fields with RISC-V-specific semantics that the
generator consumes:

- riscv_csr_addr      — 12-bit CSR address, overriding RDL's byte address.
- riscv_priv          — minimum privilege for access ("m" / "s" / "u").
                        Also accepted on addrmap / regfile as a `default`.
- riscv_wpri          — reserved bits: reads zero, writes preserved.
- riscv_warl          — write-any-read-legal legalization (bitmask or enum list).
- riscv_trap_signal   — one-cycle pulse output when the tagged field is written.
- riscv_save_on_trap  — auto-written by trap coordinator on trap entry.
- riscv_restore_on_ret — auto-restored by trap coordinator on xRET.
"""

from .csr_addr import RiscvCsrAddr
from .privilege import RiscvPriv
from .warl import RiscvWarl
from .wpri import RiscvWpri
from .trap_signal import RiscvTrapSignal
from .trap_lifecycle import RiscvSaveOnTrap, RiscvRestoreOnRet
from .clint import RiscvIntrClintRole
from .plic import RiscvIntrPlicRole

ALL_UDPS = [
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

__all__ = ["ALL_UDPS"] + [c.__name__ for c in ALL_UDPS]
