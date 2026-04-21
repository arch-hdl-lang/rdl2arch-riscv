# Build rules for the Phase-6.2 timer-ISR test program.
#
# Outputs, by default into $(BUILD):
#   timer_isr.elf   — linked ELF
#   timer_isr.bin   — flat binary (stripped of ELF metadata)
#   timer_isr.vmem  — one 32-bit word per line, ready for $readmemh
#   timer_isr.dis   — disassembly, for debugging when things go sideways
#
# Invoked from the pytest harness as:
#   make -C tests/cpu/sw BUILD=<tmp>

CROSS     ?= riscv64-elf-
AS        := $(CROSS)gcc
LD        := $(CROSS)gcc
OBJCOPY   := $(CROSS)objcopy
OBJDUMP   := $(CROSS)objdump

# RV32IMC with Zicsr so csrr/csrw assemble. We drop the C extension to
# keep disassembly readable — RV32IM-only — but any of `rv32i`, `rv32im`,
# `rv32imc` works with our SoC config (Ibex defaults enable the C ext).
MARCH     := rv32im_zicsr
MABI      := ilp32
CFLAGS    := -march=$(MARCH) -mabi=$(MABI) -nostdlib -nostartfiles \
             -ffreestanding -Os -g \
             -Wl,-T,link.ld -Wl,--no-relax
ASFLAGS   := -march=$(MARCH) -mabi=$(MABI) -c

BUILD     ?= build

ELF       := $(BUILD)/timer_isr.elf
BIN       := $(BUILD)/timer_isr.bin
VMEM      := $(BUILD)/timer_isr.vmem
DIS       := $(BUILD)/timer_isr.dis

OBJS      := $(BUILD)/timer_isr.o

.PHONY: all clean
all: $(VMEM) $(DIS)

$(BUILD):
	mkdir -p $(BUILD)

$(BUILD)/%.o: %.S | $(BUILD)
	$(AS) $(ASFLAGS) -o $@ $<

$(ELF): $(OBJS) link.ld | $(BUILD)
	$(LD) $(CFLAGS) -o $@ $(OBJS)

$(BIN): $(ELF)
	$(OBJCOPY) -O binary $< $@

$(VMEM): $(BIN) bin2vmem.py
	python3 bin2vmem.py $< $@

$(DIS): $(ELF)
	$(OBJDUMP) -d -M no-aliases $< > $@

clean:
	rm -rf $(BUILD)
