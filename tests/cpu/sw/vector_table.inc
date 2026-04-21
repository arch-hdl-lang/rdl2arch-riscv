// 32-entry vector table body. `#include`d into each `.text.trap`
// section so the dispatch for any cause 0..31 lands on a well-formed
// `j common_trap_handler`. Sized to exactly 128 bytes so the reset
// trampoline at 0x0010_0080 (see link.ld) isn't overwritten.
    j   common_trap_handler  // cause  0 / exception
    j   common_trap_handler  // cause  1
    j   common_trap_handler  // cause  2
    j   common_trap_handler  // cause  3 / M-software
    j   common_trap_handler  // cause  4
    j   common_trap_handler  // cause  5
    j   common_trap_handler  // cause  6
    j   common_trap_handler  // cause  7 / M-timer
    j   common_trap_handler  // cause  8
    j   common_trap_handler  // cause  9
    j   common_trap_handler  // cause 10
    j   common_trap_handler  // cause 11 / M-external
    j   common_trap_handler  // cause 12
    j   common_trap_handler  // cause 13
    j   common_trap_handler  // cause 14
    j   common_trap_handler  // cause 15
    j   common_trap_handler  // cause 16
    j   common_trap_handler  // cause 17
    j   common_trap_handler  // cause 18
    j   common_trap_handler  // cause 19
    j   common_trap_handler  // cause 20
    j   common_trap_handler  // cause 21
    j   common_trap_handler  // cause 22
    j   common_trap_handler  // cause 23
    j   common_trap_handler  // cause 24
    j   common_trap_handler  // cause 25
    j   common_trap_handler  // cause 26
    j   common_trap_handler  // cause 27
    j   common_trap_handler  // cause 28
    j   common_trap_handler  // cause 29
    j   common_trap_handler  // cause 30
    j   common_trap_handler  // cause 31
