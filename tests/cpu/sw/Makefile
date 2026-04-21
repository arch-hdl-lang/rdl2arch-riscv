# Build rules for the CPU bring-up programs (Phases 6.2 / 6.3).
#
# Default target builds every program named in $(PROGS) into $(BUILD),
# producing:
#   <prog>.elf   — linked ELF
#   <prog>.bin   — flat binary (stripped of ELF metadata)
#   <prog>.vmem  — one 32-bit word per line, ready for $readmemh
#   <prog>.dis   — disassembly, for debugging when things go sideways
#
# Invoked from the pytest harness as:
#   make -C tests/cpu/sw BUILD=<tmp>
# Or for a single program:
#   make -C tests/cpu/sw BUILD=<tmp> <tmp>/<prog>.vmem

CROSS     ?= riscv64-elf-
AS        := $(CROSS)gcc
LD        := $(CROSS)gcc
OBJCOPY   := $(CROSS)objcopy
OBJDUMP   := $(CROSS)objdump

# RV32IM with Zicsr so csrr/csrw assemble. No compressed extension for
# readable disassembly; any of `rv32i`, `rv32im`, `rv32imc` works with
# our SoC config.
MARCH     := rv32im_zicsr
MABI      := ilp32
CFLAGS    := -march=$(MARCH) -mabi=$(MABI) -nostdlib -nostartfiles \
             -ffreestanding -Os -g \
             -Wl,-T,link.ld -Wl,--no-relax
ASFLAGS   := -march=$(MARCH) -mabi=$(MABI) -c -I.

BUILD     ?= build

PROGS     := timer_isr sw_isr ext_isr multictx_isr mscratch_csrfile mtvec_csrfile

VMEMS     := $(PROGS:%=$(BUILD)/%.vmem)
DISES     := $(PROGS:%=$(BUILD)/%.dis)

.PHONY: all clean
all: $(VMEMS) $(DISES)

$(BUILD):
	mkdir -p $(BUILD)

# `.S` (uppercase) → GCC runs cpp first, so #includes work.
$(BUILD)/%.o: %.S common.h vector_table.inc | $(BUILD)
	$(AS) $(ASFLAGS) -o $@ $<

$(BUILD)/%.elf: $(BUILD)/%.o link.ld | $(BUILD)
	$(LD) $(CFLAGS) -o $@ $<

$(BUILD)/%.bin: $(BUILD)/%.elf
	$(OBJCOPY) -O binary $< $@

$(BUILD)/%.vmem: $(BUILD)/%.bin bin2vmem.py
	python3 bin2vmem.py $< $@

$(BUILD)/%.dis: $(BUILD)/%.elf
	$(OBJDUMP) -d -M no-aliases $< > $@

clean:
	rm -rf $(BUILD)
