# rdl2arch-riscv

RISC-V privileged CSR generator built on `rdl2arch` — consumes a SystemRDL
spec tagged with RISC-V User-Defined Properties and emits ARCH HDL modules
for the CSR file and access controller (plus the trap coordinator in a
later phase).

## Status

- ✅ Phase 1 — CSR-file module (storage + decode + WPRI / WARL legalization + trap-signal pulses).
- ✅ Phase 2 — Access-controller module (privilege check + read-only check + per-register priv overrides).
- ✅ Phase 3 — Trap coordinator (save-on-trap routing; restore-on-ret via external wiring).
- ✅ Phase 4 — Functional verification stack.
  - ✅ Phase 4a — Pybind arch-sim tests (CSR file + access controller + trap coordinator).
  - ✅ Phase 4b — Integrated-top wrapper (generated at test time) + cocotb/Verilator SV parity.
- ✅ Phase 5 — Interrupt controllers.
  - ✅ Phase 5.0 — `mip` / `mie` CSRs in the mtrap fixture.
  - ✅ Phase 5.1 — CLINT generator (`RiscvClintExporter` emits the MMIO register block + timer/msip logic module). Single-hart; multi-hart and PLIC are follow-ups.

## Install

```bash
pip install -e .
```

`rdl2arch-riscv` command available on PATH. Requires `rdl2arch >= 0.1`
(editable or released).

## Usage

```bash
rdl2arch-riscv my_csrs.rdl -o out/
# emits:
#   out/<Name>CsrFilePkg.arch   — shared types (CSR addr enum + per-reg structs + hwif structs)
#   out/<Name>CsrFile.arch      — CSR file module
#   out/<Name>CsrAccess.arch    — access controller module

arch build out/*.arch
```

## UDPs

Register every UDP with the `systemrdl-compiler` before calling
`compile_file`. The library ships the full set under
`rdl2arch_riscv.udps.ALL_UDPS`:

```python
from systemrdl import RDLCompiler
from rdl2arch_riscv.udps import ALL_UDPS
from rdl2arch_riscv import RiscvCsrExporter

rdlc = RDLCompiler()
for udp in ALL_UDPS:
    rdlc.register_udp(udp, soft=False)     # <-- NOTE: soft=False REQUIRED
rdlc.compile_file("my_csrs.rdl")
RiscvCsrExporter().export(rdlc.elaborate().top, "out/")
```

> ⚠️ **`soft=False` is required.** `register_udp`'s default `soft=True`
> registers the UDP into the compiler but hides it from the RDL parser's
> property-lookup path — `rdlc.compile_file` then errors with "Unrecognized
> property 'riscv_wpri'" even though the UDP object was accepted.
> Always register RISC-V UDPs with `soft=False`. The CLI handles this
> automatically; only direct library users need to remember it.

### UDP cheat sheet

| UDP | Valid on | Type | Purpose |
|---|---|---|---|
| `riscv_csr_addr`      | Reg                       | `int` (0..0xFFF) | 12-bit RISC-V CSR address (overrides RDL byte address) |
| `riscv_priv`          | Addrmap / Regfile / Reg / Field | `"m"` / `"s"` / `"u"` | Minimum privilege for access. Propagates via `default` from addrmap. |
| `riscv_wpri`          | Field                     | `bool` | Reserved bits: reads zero, writes silently discarded |
| `riscv_warl`          | Field                     | `str` | Bitmask (`"0x1F"`) or enum list (`"0,1,3"`) legalization |
| `riscv_trap_signal`   | Reg / Field               | `str` | Name of a one-cycle pulse port that asserts on write |
| `riscv_save_on_trap`  | Field                     | `bool` | Auto-written by trap coordinator on trap entry (wired in Phase 3) |
| `riscv_restore_on_ret`| Field                     | `bool` | Auto-restored by trap coordinator on xRET (wired in Phase 3) |
| `riscv_intr_clint_role`| Reg                      | `"msip"` / `"mtimecmp_lo"` / `"mtimecmp_hi"` / `"mtime_lo"` / `"mtime_hi"` | CLINT reg role — used by `RiscvClintExporter` |

## CSR address conventions

Two supported shapes; `riscv_csr_addr` is the recommended one:

**Recommended:** use `riscv_csr_addr = 0x300;` directly — RDL `@` is a
dummy non-overlapping byte layout that the scanner ignores.

```
reg {
    riscv_csr_addr = 0x300;
    field { ... } mie[3:3];
} mstatus @ 0x0;
```

**Legacy (also supported):** omit `riscv_csr_addr` and place the reg at
`csr_addr << 2` in RDL byte space. Scanner divides by 4.

```
reg {
    field { ... } mie[3:3];
} mstatus @ 0xC00;           // RISC-V CSR 0x300
```

Either produces the same emitted match arms (`12'h300 => ...`).

## License

Apache 2.0.
