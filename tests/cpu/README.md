# Ibex SoC integration

Takes the CLINT + PLIC HDL we emit (via `RiscvClintExporter` and
`RiscvPlicExporter`) and drops it into a minimal SoC around a real
RV32IMC core — lowRISC's [Ibex](https://github.com/lowRISC/ibex) — to
prove our generator stack composes with a CPU whose interrupt interface
matches the RISC-V privileged spec.

## Phases

| Phase | What it proves | Test |
|------:|---|---|
| 6.1 | The combined HDL elaborates and type-checks under Verilator. Port widths, hwif struct fields, bus directions all agree. | `test_soc_lint.py` |
| 6.2 | A hand-written RV32 timer-ISR program runs end-to-end on Ibex + our CLINT. Sets `mtvec`, `mie.MTIE`, `mtimecmp`, `mstatus.MIE`; busy-waits; traps into the vector table; handler stashes `mcause` / `mepc` / `mip`; returns via `mret`; asserts `mcause == 0x8000_0007`. | `test_cpu_programs.py[timer_isr]` |
| 6.3 | Two more programs under the same harness: `sw_isr.S` (CLINT `msip` → `mip.MSIP`, handler acks by writing zero) and `ext_isr.S` (external source via `ext_irq_sources_i[2]` → PLIC arbitration → `mip.MEIP`, handler does claim-read + complete-write against `PLIC.claim_0`). | `test_cpu_programs.py[sw_isr]`, `[ext_isr]` |
| 6.4 | Multi-context PLIC: SoC uses `plic_multictx` (2 M-mode contexts). `intr_out[0]` → `irq_external_i` (cause 11), `intr_out[1]` → `irq_fast_i[0]` (cause 16, standing in for S-mode SEIP on this M-only core). `multictx_isr.S` configures source 3 on ctx 0 + source 5 on ctx 1, cocotb raises both; the handler dispatches on `mcause`, claims/completes each through its own context. | `test_cpu_programs.py[multictx_isr]` |

## Layout

```
soc/
├── ibex_mini_soc.sv     # top — Ibex + RAM + simctrl + our CLINT + our PLIC
└── obi_to_axi_lite.sv   # OBI (Ibex-style) <-> AXI4-Lite slave bridge,
                         # one instance per MMIO device
conftest.py              # session-scoped fixture: generates CLINT+PLIC SV,
                         # resolves Ibex dep tree via fusesoc, returns a
                         # combined filelist
test_soc_lint.py         # runs verilator --lint-only on the combined tree
```

The memory map matches SiFive conventions where reasonable:

```
0x0002_0000 + 1 kB  simulator_ctrl  (halt / log via $write DPI)
0x0010_0000 + 1 MB  RAM             (.text + .data; Ibex boots at base)
0x0200_0000 + 64 kB CLINT           (16-bit AXI addr)
0x0C00_0000 + 4 MB  PLIC            (22-bit AXI addr)
```

## External deps

* **lowRISC Ibex** at `$IBEX_ROOT` (default `~/github/ibex`). Tests
  `pytest.skip` when absent.
* **fusesoc** (`pip install fusesoc`) — resolves Ibex's core-file
  dependency tree and writes a Verilator `.vc`.
* **verilator >= 4.210** (we use 5.034).
* **`riscv64-elf-gcc`** (homebrew formula) — only used by Phase 6.2+.

## What's different from Ibex's `ibex_simple_system`

`ibex_simple_system` ships its own memory-mapped `timer` module; we
replace it with the generated `Clint` register block + `ClintLogic`
sibling (drives `msip_out` / `mtip_out`). The PLIC is new — Ibex's
example has no external-interrupt path wired past `irq_external_i = 0`.

We also roll our own one-hot address decoder in `ibex_mini_soc` rather
than reuse `shared/rtl/bus.sv`: our OBI→AXI bridge backpressures via
`gnt` to serialize transactions for the rdl2arch-emitted slave, and the
stock `bus.sv` unconditionally grants (ignores device-side gnt).
