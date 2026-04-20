"""rdl2arch-riscv command-line entry point."""

import argparse
import sys

from systemrdl import RDLCompileError, RDLCompiler

from .exporter import RiscvCsrExporter
from .udps import ALL_UDPS


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(
        prog="rdl2arch-riscv",
        description="Generate a RISC-V CSR file ARCH module from SystemRDL input.",
    )
    p.add_argument("input", help="SystemRDL input file (.rdl)")
    p.add_argument("-o", "--output-dir", default=".", help="Output directory")
    p.add_argument("--module-name", help="Override generated module name")
    p.add_argument("--package-name", help="Override generated package name")
    p.add_argument("--xlen", type=int, default=32, choices=[32, 64],
                   help="RISC-V XLEN (default 32)")
    args = p.parse_args(argv)

    rdlc = RDLCompiler()
    for udp in ALL_UDPS:
        # soft=False: the UDP is immediately active in RDL parsing without the
        # user needing a `default property ...` declaration. Soft UDPs are
        # silently ignored unless re-declared in the RDL itself.
        rdlc.register_udp(udp, soft=False)
    try:
        rdlc.compile_file(args.input)
        root = rdlc.elaborate()
    except RDLCompileError:
        return 1

    files = RiscvCsrExporter().export(
        root.top,
        args.output_dir,
        module_name=args.module_name,
        package_name=args.package_name,
        xlen=args.xlen,
    )
    for _, path in files.items():
        print(path)
    return 0


if __name__ == "__main__":
    sys.exit(main())
