#!/usr/bin/env python3
"""Convert a flat binary into a Verilog $readmemh-friendly vmem.

One 32-bit little-endian word per output line. No `@addr` markers —
callers are expected to $readmemh at byte offset 0 of the target
memory.  Trailing bytes are zero-padded to a 4-byte boundary.

Usage: ./bin2vmem.py in.bin out.vmem
"""

from __future__ import annotations

import struct
import sys


def main(argv: list[str]) -> int:
    if len(argv) != 3:
        print("usage: bin2vmem.py <in.bin> <out.vmem>", file=sys.stderr)
        return 2
    data = open(argv[1], "rb").read()
    pad = (-len(data)) % 4
    if pad:
        data += b"\x00" * pad
    with open(argv[2], "w") as fh:
        for i in range(0, len(data), 4):
            (word,) = struct.unpack("<I", data[i:i + 4])
            fh.write(f"{word:08x}\n")
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv))
