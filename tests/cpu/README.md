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
| 6.5a | **First M-trap CSR swap-in.** Fork of `ibex_cs_registers.sv` lives in `soc/ibex_cs_registers_hybrid.sv`; upstream's copy is filtered out of the fusesoc filelist. Routes `mscratch` (CSR 0x340) through the generated `MTrapIbexCsrFile` instanced inside the fork. `mscratch_csrfile.S` exercises two write/readback round-trips and cocotb peeks `u_ourfile.mscratch_r.value` hierarchically to confirm the state lives in our file, not in any Ibex shadow. | `test_cpu_programs.py[mscratch_csrfile]` |
| 6.5b | Adds **`mtvec`** (CSR 0x305) to the same `MTrapIbexCsrFile` — upstream's `u_mtvec_csr` removed. `csr_mtvec_o` (consumed by Ibex's if_stage for trap-PC calc) is driven from `{hwif_out.mtvec_base, hwif_out.mtvec_mode}`. Ibex's `csr_mtvec_init_i` pulse is intercepted in the adapter and replayed as a bus WRITE with `{boot_addr[31:8], 6'b0, 2'b01}`. `mtvec_csrfile.S` reads the init-replay value + round-trips two more patterns, cocotb decodes the packed `mtvec_r` struct to validate. The 4 interrupt-driven programs still work: their `_start` csrw mtvec now flows through our CsrFile and every trap PC is computed from our hwif_out. | `test_cpu_programs.py[mtvec_csrfile]` |
| 6.5c | Adds **`mepc`, `mcause`, `mtval`** — the HW-written-on-trap set. `MTrapIbexCsrTrapCoord` is instanced alongside the CsrFile: on `csr_save_cause_i` it drives `hwif_in_drive.*` from `exception_pc` / `csr_mcause_i` / `csr_mtval_i`; every other cycle it feeds `hwif_out` back so storage holds. Ibex's packed `exc_cause_t` is encoded into the flat 32-bit layout our CsrFile stores. Also fixes a cross-bus op mapping: Ibex pre-computes SET/CLEAR values into `csr_wdata_int`, so the adapter collapses every non-READ op to a plain WRITE to avoid double-application. `mepc_mcause_mtval_csrfile.S` covers SW writes / reads; the 4 interrupt programs are the real HW-save proof — their handlers read `mcause`/`mepc` from our CsrFile after every trap. | `test_cpu_programs.py[mepc_mcause_mtval_csrfile]` |
| 6.5d | Adds **`mstatus`** (mie / mpie / mpp). The TrapCoord handles the save side (mpie ← old mie, mpp ← priv_lvl_q on `csr_save_cause_i`); the adapter folds `mie` auto-clear on save and the full mret restore (mie ← mpie, mpie ← 1, mpp ← U) into the `hwif_in_live` input that the coord passes through on non-save cycles. Ibex's controller reads `mie` out of `hwif_out`. `mprv`/`tw` are M-only-SoC dead letters — `csr_mstatus_tw_o` tied low, `priv_mode_lsu_o` drops the mprv gate. `mstatus_csrfile.S` uses an `ecall` to drive the full state machine (reset → csrs → trap → mret → readback) and cocotb decodes the packed `mstatus_r` to assert the post-mret `mie=1/mpie=1/mpp=U` state. | `test_cpu_programs.py[mstatus_csrfile]` |
| 6.5e | Adds **`mie`** (CSR 0x304) + **`mip`** (CSR 0x344) — closing out Phase 6.5. Upstream's `u_mie_csr` removed; mie storage lives in our CsrFile and Ibex's controller reads enable bits out of `hwif_out.mie_*` to build `irqs_o = mip & mie_live` combinationally. mip is SW-readonly by design: the adapter drives `hwif_in_live.mip_*` from the live `irq_*_i` module inputs every cycle, so SW reads see a 1-cycle-lagged mirror while the controller's trap-decision path taps `irq_*_i` directly (no storage in the middle, matching upstream latency). The internal `mip` wire is preserved as a combinational alias of the IRQ inputs so Ibex's RVFI hierarchical references still resolve. `mie_mip_csrfile.S` covers the SW-visible paths — csrrw / csrrc / csrrs round-trip on mie (MSIE/MTIE/MEIE/MFIE[0]); CLINT.msip write → mip.MSIP mirror → CLINT.msip clear → mip.MSIP drop — while the 4 interrupt-driven programs continue to prove the enable/controller path end-to-end. | `test_cpu_programs.py[mie_mip_csrfile]` |
| 6.5f | **mret restore becomes a generator feature.** Phase-6.5d had the adapter hand-compute the three mstatus restore values (`mie ← mpie`, `mpie ← 1`, `mpp ← U`) and funnel them through `hwif_in_live.mstatus_*` because the TrapCoord only modeled save-on-trap. The generator now emits `xret_enter` + `restore_<reg>_<field>` ports symmetric with `trap_enter` + `save_<reg>_<field>`; fields tagged `riscv_restore_on_ret` get their `hwif_in_drive.<m>` muxed from the restore port on the xret cycle. Adapter loses its restore-priority cascade and just wires three one-liners: `restore_mstatus_mie ← hwif_out.mstatus_mpie`, `restore_mstatus_mpie ← 1`, `restore_mstatus_mpp ← U`. `mstatus_csrfile.S` keeps passing untouched (the SW-visible state machine is identical). | same — `mstatus_csrfile` exercise is unchanged; change is compile-time shape only |

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
