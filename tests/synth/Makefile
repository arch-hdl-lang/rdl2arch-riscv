# Yosys synth flow for the Ibex hybrid SoC against Nangate45.
#
# `make synth` is the smoke-test entry point: regenerates CLINT/PLIC/CsrFile
# via arch-com, gathers Ibex's SV via fusesoc, and runs Yosys → ABC →
# stat. Outputs land in ./build/.
#
# Set NANGATE45_ROOT / IBEX_ROOT / ARCH_BIN env vars to override defaults
# (see synth.py docstring for the full list).

PYTHON ?= python3

.PHONY: synth synth-ibex-top synth-soc clean help

help:
	@echo "Targets:"
	@echo "  synth           — synth full SoC (top = ibex_mini_soc)"
	@echo "  synth-ibex-top  — core-only synth (top = ibex_top); matches paper claim"
	@echo "  synth-soc       — alias for synth"
	@echo "  clean           — delete ./build/"
	@echo ""
	@echo "Env overrides:"
	@echo "  NANGATE45_ROOT  (default: \$$HOME/pdks/nangate45)"
	@echo "  IBEX_ROOT       (default: \$$HOME/github/ibex)"
	@echo "  ARCH_BIN        (default: \$$HOME/github/arch-com/target/release/arch)"
	@echo "  SYNTH_BUILD_DIR (default: ./build)"

synth synth-soc:
	$(PYTHON) synth.py --top ibex_mini_soc

synth-ibex-top:
	$(PYTHON) synth.py --top ibex_top

clean:
	rm -rf build/
