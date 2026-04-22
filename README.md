# rdl2arch-riscv

RISC-V privileged CSR generator built on `rdl2arch` — consumes a SystemRDL
spec tagged with RISC-V User-Defined Properties and emits ARCH HDL modules
for the CSR file and access controller (plus the trap coordinator in a
later phase).

## Status

- ✅ Phase 1 — CSR-file module (storage + decode + WPRI / WARL legalization + trap-signal pulses).
- ✅ Phase 2 — Access-controller module (privilege check + read-only check + per-register priv overrides).
- ✅ Phase 3 — Trap coordinator. Three families of generator-emitted ports routing data into `hwif_in_drive`:
    - `riscv_save_on_trap` — emits `save_<reg>_<field>: in UInt<W>` per tagged field + a `trap_enter` pulse; on the enter cycle the TrapCoord muxes `hwif_in_drive` from the save port.
    - `riscv_restore_on_ret` — mirrors the save shape on the xret side with `restore_<reg>_<field>: in UInt<W>` + `xret_enter` pulse; priority `trap_enter > xret_enter > hwif_in_live` on fields that carry both tags.
    - `riscv_hw_mirror` — emits `mirror_<reg>_<field>: in UInt<W>` and drives `hwif_in_drive` unconditionally from it (always-on live mirror of an external wire — `mip.msip ← irq_software_i` etc.). Mutually exclusive with save / restore (the validator rejects mixing event-gated and always-on drives on the same field).

    The pipeline supplies all three kinds of data; the generator only routes / muxes / priority-orders. No architecture-specific knowledge lives in the generator. On the output side, every register also exports a `<reg>_rdata_flat: UInt<xlen>` member on `hwif_out` — the spec-layout view of the whole register (the same expression the SW readback mux returns). Adapters can consume this instead of depending on the per-field packed-struct naming, so they stay durable against RDL field renames / repacks.
- ✅ Phase 4 — Functional verification stack.
  - ✅ Phase 4a — Pybind arch-sim tests (CSR file + access controller + trap coordinator).
  - ✅ Phase 4b — Integrated-top wrapper (generated at test time) + cocotb/Verilator SV parity.
- ✅ Phase 5 — Interrupt controllers.
  - ✅ Phase 5.0 — `mip` / `mie` CSRs in the mtrap fixture.
  - ✅ Phase 5.1 — CLINT generator (`RiscvClintExporter` emits the MMIO register block + timer/msip logic module). Single-hart; multi-hart is a follow-up.
  - ✅ Phase 5.2 — PLIC generator (`RiscvPlicExporter` emits the MMIO register block + priority-arbitration logic module). Level-triggered sources.
  - ✅ Phase 5.2a — Multi-context PLIC. Arbiter replicates per context; output is a `UInt<N_contexts>` bitmap. Fixtures shipped: `plic_basic` (1 context), `plic_multictx` (2 contexts: M + S).
  - ✅ Phase 5.2b — Spec-compliant claim / complete. Per-context in-service bitmap: a SW **read** of the claim reg latches the returned source as in-service (masks it from further arbitration on this context); a SW **write** clears the matching bit. Consumes upstream `emit_read_pulse` / `emit_write_pulse` UDPs so no side-channel is needed. Edge detection remains a follow-up.
- 🚧 Phase 6 — CPU integration (lowRISC Ibex + our CLINT/PLIC as a SoC).
  - ✅ Phase 6.1 — SoC scaffold: `ibex_mini_soc.sv` (top), `obi_to_axi_lite.sv` (single-transaction OBI↔AXI4-Lite bridge), memory map for RAM + simulator_ctrl + CLINT + PLIC. Verilator `--lint-only` passes — generated HDL composes with a real RV32IMC core. See `tests/cpu/`.
  - ✅ Phase 6.2 — Timer-ISR end-to-end. Hand-written RV32 program (`tests/cpu/sw/timer_isr.S`) sets up `mtvec` + `mie.MTIE` + CLINT `mtimecmp` + `mstatus.MIE`, busy-waits; cocotb testbench releases reset, waits for the program to hit a completion sentinel in RAM, and asserts `mcause == 0x80000007` (M-timer interrupt bit), `mip.MTIP == 1` at trap entry, and `mepc` inside the busy-wait loop. Drives the full path: `ClintLogic.mtip_out` → `ibex.irq_timer_i` → trap → vector table → handler → `mret`.
  - ✅ Phase 6.3 — Software-interrupt (`sw_isr.S`) and external-interrupt (`ext_isr.S`) variants. The external test is the most interesting: it drives `ext_irq_sources_i[2]` from cocotb after the program writes a `ready_for_irq` sentinel, the PLIC winner-ID is read by the handler (auto-claiming on our PLIC), the claim-id is written back to complete — proving the claim/complete handshake works from a real RISC-V ISR on top of the register block's `emit_read_pulse`/`emit_write_pulse` wiring.
  - ✅ Phase 6.4 — Multi-context PLIC on the CPU (`multictx_isr.S`). SoC now uses the 2-context `plic_multictx` fixture; `intr_out[0]` drives Ibex's `irq_external_i` (cause 11) while `intr_out[1]` is routed to `irq_fast_i[0]` (cause 16) as a stand-in for the missing S-mode "SEIP" pin on an M-only core. One cocotb test raises two external sources simultaneously, handler dispatches on `mcause`, claims + completes each through its own PLIC context — proving per-context `claimed_r` independence + correct SoC-level routing.
  - ✅ Phase 6.5 — Swap Ibex's M-trap CSRs onto our generated `CsrFile`. Ibex's `ibex_cs_registers.sv` is forked in-tree (`tests/cpu/soc/ibex_cs_registers_hybrid.sv`) and upstream's copy is filtered out of the fusesoc filelist. The fork keeps the upstream module name so `ibex_core.sv` binds it unchanged. One `MTrapIbexCsrFile` instance grows across sub-phases, gaining more CSR storage each step.
  - 🚧 Phase 6.6 — Counter-side CSRs. Expands the hybrid to cover `mcountinhibit` + the `mcycle`/`mcycleh` pair. `minstret`/`minstreth` stay on Ibex's native path because of the core's speculative-retire +1 optimization, which is pipeline-specific and doesn't belong in the generator.
    - ✅ Phase 6.6a — **`mcountinhibit`** migrated. Plain RW register (CSR 0x320); no new generator surface needed. The adapter keeps a `mcountinhibit` wire for the downstream HPM counter-gating logic, now sourced from `hwif_out.mcountinhibit_rdata_flat` (the spec-layout view added in PR #29). Bit 1 (`tm`) and bits 13..31 are WPRI; the remaining 11 bits (`cy`, `ir`, `hpm[12:3]` matching `MHPMCounterNum=10`) are sw-writable storage in our CsrFile. A new `mcountinhibit_csrfile.S` round-trips the mask behaviour and confirms WPRI bits drop writes.
    - ✅ Phase 6.6b — **`mcycle` + `mcycleh`** migrated. New generator feature: `riscv_hw_increment_when = "<port>"` on a field emits that port on the CsrFile plus an `if <port> -> state <= state +% 1` line in the seq block. A linked high-half field uses `riscv_hw_increment_high_of = "<low_reg>"` to carry on low-half rollover. Fields are `sw = rw; hw = r` (no hwif_in) since the CsrFile self-drives storage; SW writes still land via the bus and override the increment on the same cycle via seq last-write-wins. Adapter wires `cycle_en = mhpmcounter_incr[0] & ~mcountinhibit[0]` — freezing behaviour via `mcountinhibit.cy` works end-to-end. `mcycle_csrfile.S` covers: running counter advances, frozen counter holds, SW write wins on both halves. `minstret` / `minstreth` intentionally stay on Ibex's native path — the core's speculative-retire +1 optimization is pipeline-specific and doesn't generalize.
  - 🚧 Phase 6.7 — **Debug CSRs** (`dcsr`, `dpc`, `dscratch0`, `dscratch1`). Ibex always instantiates these four even with `DbgTriggerEn=0` (the flag only gates the trigger CSRs). Storage moves off upstream's four `ibex_csr` instances onto our `MTrapIbexCsrFile`; WPRI / RO-constant (`xdebugver`=0x4) / WARL (`prv` ∈ {U, M}) semantics all go in the RDL, so the generator enforces bit-forcing the adapter used to do by hand. The downstream hwif_out consumers (`csr_depc_o`, `debug_{single_step, ebreakm, ebreaku}_o`, `priv_lvl_d = priv_lvl_e'(dcsr_prv)` on `dret`) all tap through named members. Debug CSRs are architecturally inaccessible from M-mode so no SW-csrr exercise is possible from our test programs; correctness is attested by every pre-6.7 program continuing to pass + a run-to-completion sanity test that confirms the four register nodes resolve at the expected hierarchical path.
    - ✅ Phase 6.5a — **`mscratch`** migrated. Upstream's `u_mscratch_csr` removed entirely; mscratch (CSR 0x340) storage now lives in the generated `MTrapIbexCsrFile`. A `mscratch_csrfile.S` program round-trips two patterns; cocotb asserts the readbacks match AND peeks the CsrFile's `mscratch_r.value` hierarchically to confirm the data landed in our state.
    - ✅ Phase 6.5b — **`mtvec`** migrated. Upstream's `u_mtvec_csr` removed. The top-level `csr_mtvec_o` output (consumed by Ibex's if_stage for trap-PC calc) is now driven from `{hwif_out.mtvec_base, hwif_out.mtvec_mode}`. Ibex's post-reset `csr_mtvec_init_i` pulse is intercepted in the adapter and replayed as a bus WRITE with `{boot_addr[31:8], 6'b0, 2'b01}` so boot semantics match upstream. The 4 interrupt-driven cpu programs (timer/sw/ext/multictx) still pass end-to-end — each of their `csrw mtvec` calls now flows through our CsrFile and every subsequent trap PC computation reads it back via the hwif_out wiring. `mtvec_csrfile.S` adds a focused read/write round-trip check plus an init-pulse readback.
    - ✅ Phase 6.5c — **`mepc`, `mcause`, `mtval`** migrated. Ibex's `u_{mepc,mcause,mtval}_csr` instances deleted. HW-save path: the generated `MTrapIbexCsrTrapCoord` muxes `hwif_in_drive.*` between a self-loop from `hwif_out` (hold-steady on non-trap cycles) and Ibex's `exception_pc` / `csr_mcause_i` / `csr_mtval_i` when `csr_save_cause_i` pulses. Ibex's packed `exc_cause_t` is re-encoded into the flat 32-bit shape our CsrFile stores. Also surfaced a cross-bus op-collapse bug: Ibex pre-computes SET/CLEAR into `csr_wdata_int`, so we forward every non-READ op to the CsrFile as a plain WRITE to avoid re-applying the operation twice. The 4 interrupt programs are the real proof — their handlers read `mcause`/`mepc` after every trap, and those values all come from our CsrFile's storage.
    - ✅ Phase 6.5d — **`mstatus`** (mie / mpie / mpp) migrated. Upstream's `u_mstatus_csr` removed. The full trap-entry / mret state machine now runs through our CsrFile: `csr_save_cause_i` auto-clears `mie` and stashes the old value into `mpie`; `csr_restore_mret_i` restores `mie ← mpie`, `mpie ← 1`, `mpp ← U`. The TrapCoord handles the save side for `mpie` / `mpp`; the adapter folds the mret restore + `mie` auto-clear into the `hwif_in_live` input (which the coord passes through on non-save cycles). Ibex's controller now reads the `mie` bit that gates interrupts out of `hwif_out.mstatus_mie`. `mprv` and `tw` aren't modeled (our M-only SoC doesn't use them — `csr_mstatus_tw_o` tied low; `priv_mode_lsu_o` drops its `mprv` gating). A new `mstatus_csrfile.S` fires an `ecall` and asserts the full cycle observable from SW: post-reset → csrs mie → trap → mie=0,mpie=1,mpp=M inside → mret → mie=1,mpie=1,mpp=U after.
    - ✅ Phase 6.5e — **`mie` + `mip`** migrated. Upstream's `u_mie_csr` removed; mie (CSR 0x304) storage lives entirely in our CsrFile — SW writes land there, and the Ibex controller's `irqs_o = mip & mie` path now reads the enable bits out of `hwif_out.mie_*` combinationally. mip (CSR 0x344) is SW-readonly by design: the adapter drives `hwif_in_live.mip_*` from the live `irq_*_i` module inputs every cycle, so SW reads see a 1-cycle-lagged mirror (plenty of slack for any realistic handler) while the controller's trap-decision path taps `irq_*_i` directly with no storage in the middle. The internal `mip` wire (a combinational alias of the IRQ inputs) is preserved so Ibex's RVFI hierarchical references resolve. `mie_mip_csrfile.S` covers the focused SW-visible paths (csrrw / csrrc / csrrs round-trip on mie; CLINT.msip → mip.MSIP mirror + clearing); the 4 interrupt-driven programs continue to prove the enable/controller path end-to-end. All 7 M-trap CSRs (mscratch, mtvec, mepc, mcause, mtval, mstatus, mie/mip) are now served by our generator.
    - ✅ Phase 6.5f — **restore-on-ret** promoted to a TrapCoord feature. Phase 3's `riscv_restore_on_ret` UDP was declared-but-unused: the Phase-6.5d adapter folded the mret restore (`mie ← mpie`, `mpie ← 1`, `mpp ← U`) into three `hwif_in_live.mstatus_*` muxes. The generator now emits `xret_enter` + `restore_<reg>_<field>` ports symmetric with the save side, with priority `trap_enter > xret_enter > live` on fields that carry both tags. `mstatus.mie` gained `riscv_restore_on_ret = true` in the fixture so it gets its own `restore_mstatus_mie` port (data sourced externally from `hwif_out.mstatus_mpie` — same as before, just wired through a named port rather than hand-muxed). The Ibex adapter loses ~15 lines of mret-restore logic; all 99 tests pass, yosys+sky130 re-synth shows a small combinational win (`ibex_top` 86,816 vs 87,345 from Phase-6.5e hybrid; same sequential area).

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
| `riscv_save_on_trap`  | Field                     | `bool` | Auto-written by trap coordinator on trap entry via `save_<reg>_<field>` input port + `trap_enter` pulse |
| `riscv_restore_on_ret`| Field                     | `bool` | Auto-restored by trap coordinator on xRET via `restore_<reg>_<field>` input port + `xret_enter` pulse |
| `riscv_hw_mirror`     | Field                     | `bool` | Field storage tracks a live external signal via `mirror_<reg>_<field>` input port; mutually exclusive with save / restore |
| `riscv_hw_increment_when`    | Field | `str`  | Field is a counter; generator emits the named port on CsrFile and auto-increments storage whenever that port is high |
| `riscv_hw_increment_high_of` | Field | `str`  | Field is the high half of a 64-bit split counter; links to the named low-half reg — generator increments on low rollover |
| `riscv_intr_clint_role`| Reg                      | `"msip"` / `"mtimecmp_lo"` / `"mtimecmp_hi"` / `"mtime_lo"` / `"mtime_hi"` | CLINT reg role — used by `RiscvClintExporter` |
| `riscv_intr_plic_role` | Reg                      | `"priority"` / `"pending"` / `"enable"` / `"threshold"` / `"claim"` | PLIC reg role — used by `RiscvPlicExporter` |
| `emit_read_pulse`     | Reg                       | `bool` | Upstream rdl2arch UDP; required on PLIC claim regs to drive claim latching |
| `emit_write_pulse`    | Reg                       | `bool` | Upstream rdl2arch UDP; required on PLIC claim regs to drive complete clearing |

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
