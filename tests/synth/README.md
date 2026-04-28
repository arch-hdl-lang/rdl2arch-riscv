# Yosys synthesis flow — Nangate45

Synthesizes the **Ibex hybrid SoC** (lowRISC Ibex + our generated CSR
file, trap coordinator, CLINT, and PLIC) to a Nangate45 gate-level
netlist using open-source Yosys + ABC. Single-machine reproducibility:
`make synth` from a clean checkout produces a gate count.

This is a smoke / regression flow, not a tape-out flow — Nangate45 is an
*open* 45 nm cell library used for academic and open-source synth
comparisons, not a real foundry PDK. The headline value is "the
generated RTL flows clean through standard synthesis," plus a stable
gate-count baseline that catches generator regressions.

## Install Nangate45

The Nangate library lives inside the OpenROAD-flow-scripts repo. We
sparse-checkout just the platform directory:

```bash
mkdir -p ~/pdks
cd ~/pdks
git clone --depth 1 --filter=blob:none --sparse \
    https://github.com/The-OpenROAD-Project/OpenROAD-flow-scripts.git _orfs_tmp
cd _orfs_tmp
git sparse-checkout set flow/platforms/nangate45
mv flow/platforms/nangate45 ~/pdks/nangate45
cd ~ && rm -rf ~/pdks/_orfs_tmp
```

Add to your shell rc (or set per-invocation):

```bash
export NANGATE45_ROOT="$HOME/pdks/nangate45"
```

The flow looks for `$NANGATE45_ROOT/lib/NangateOpenCellLibrary_typical.lib`.

## Other prerequisites

- **Yosys** (≥ 0.30 recommended; tested on 0.64) — `brew install yosys`
  on macOS, or your distro's package.
- **fusesoc** — `pip install fusesoc`. Used to resolve Ibex's SV
  dependency tree the same way the cocotb tests do, so the synth flow
  can't drift from what the simulation tests verify.
- **lowRISC Ibex** — `git clone https://github.com/lowRISC/ibex
  ~/github/ibex`, or set `IBEX_ROOT=/path/to/ibex`.
- **arch-com** — built from `~/github/arch-com` (`cargo build --release
  --bin arch`), or any path passed via `ARCH_BIN`.

## Run

```bash
cd tests/synth
make synth                # full SoC; top = ibex_mini_soc
make synth-ibex-top       # core-only; top = ibex_top
make help
```

Outputs land in `tests/synth/build/`:

| File | Contents |
|---|---|
| `synth.f` | Yosys file list (auto-generated, sources of truth: `synth.py` + fusesoc) |
| `synth.json` | Post-ABC gate-level netlist |
| `synth.stat` | `stat -liberty …` cell count + chip area |
| `synth.log` | Full Yosys stdout/stderr |

The console summary at end of `make synth` extracts the headline numbers
(cells, wires, area) from `synth.stat`.

## Why Nangate45, not Sky130?

Both work; the historical reason for Nangate45 here is that lowRISC
Ibex's reference synth flow uses it, so the gate counts compare cleanly
against published Ibex numbers. Sky130 is the better target for actual
silicon (real PDK, real foundry). When we re-do the synth claim in the
paper draft (`docs/paper.md`) we should report both — Nangate45 for
historical comparison, Sky130 for tape-out relevance.

## What this guards against

- **Generator regressions**: a change to `rdl2arch` or `rdl2arch-riscv`
  that produces SV that doesn't synthesize cleanly (bad latch
  inference, multi-driver, unconstrained X-state) breaks `make synth`.
- **Drift from sim**: the file list comes from the same fusesoc setup
  the cocotb tests use; we can't accidentally synthesize a different
  RTL than we simulate.
- **Stale gate-count claims**: `synth.stat` is the source of truth for
  any "the generated SoC is X cells" assertion in docs / papers.

## Latest baseline (2026-04-27)

Captured against `arch-com` `954c6c0` + `rdl2arch` PR #8 head + Ibex
`master` branch as resolved by fusesoc.

| Target | Cells | Sequential (DFF*) | Chip area (Nangate45 typ) | Inferred latches |
|---|---:|---:|---:|---:|
| `ibex_top` | 18,489 | 2,567 | 32,895 µm² | 1 |

The single `$_DLATCH_N_` traces to Ibex's hand-instantiated low-power
latch (intentional; not from rdl2arch-emitted RTL). Re-run after each
generator change and update this table; the previous Phase-6.5f notes
in the top-level README report a slightly different number because
they were taken against the bare hybrid (no full fusesoc resolve).
