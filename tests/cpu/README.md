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
| 6.2 | A hand-written RV32 timer-ISR program runs end-to-end: set `mtvec`, enable `mie.MTIE` / `mstatus.MIE`, write `mtimecmp`, WFI, trap, confirm `mcause=7`, `mret`, observe completion flag in RAM. | *planned* |
| 6.3 | Software (`msip`) and external (PLIC source + claim/complete from the ISR) interrupts handled the same way. | *planned* |

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
