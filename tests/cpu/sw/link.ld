/* Linker script for the Ibex timer-ISR bring-up program.
 *
 * RAM is mapped at 0x0010_0000 in `ibex_mini_soc`, and Ibex starts
 * executing at boot_addr + 0x80 = 0x0010_0080 after reset. We place
 * the reset trampoline there, put the trap handler at the start of
 * RAM (and point `mtvec` at it), and keep a flat `.data` section a bit
 * further up so the two don't collide.
 *
 * Everything is resolved statically — no GOT, no PLT, no libc — so the
 * .bin produced by `objcopy` can be loaded verbatim as words into RAM
 * at offset 0.
 */

ENTRY(_reset)

MEMORY
{
  RAM (rwx) : ORIGIN = 0x00100000, LENGTH = 1M
}

SECTIONS
{
  /* Trap handler lives at the start of RAM; mtvec = 0x0010_0000. */
  .text.trap 0x00100000 : {
    *(.text.trap)
  } > RAM

  /* Pad to Ibex's reset vector (+0x80). */
  .text.reset 0x00100080 : {
    *(.text.reset)
  } > RAM

  .text 0x00100100 : {
    *(.text .text.*)
  } > RAM

  .rodata ALIGN(4) : {
    *(.rodata .rodata.*)
  } > RAM

  .data 0x00101000 : {
    *(.data .data.*)
    *(.sdata .sdata.*)
  } > RAM

  .bss ALIGN(4) : {
    *(.bss .bss.*)
    *(.sbss .sbss.*)
    *(COMMON)
  } > RAM

  /* 4 KB stack at top of first 1 MB. */
  _stack_top = 0x001F0000;
}
