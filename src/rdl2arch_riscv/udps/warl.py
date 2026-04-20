"""riscv_warl — Write-Any-Read-Legal legalization.

The tagged field accepts any software write but hardware coerces the
written value into a subset of "legal" values. Two forms are supported:

  - Bitmask: `"0x1F"` — only the bits set in the mask are retained.
    new_value = wdata & mask. Common for MXLEN / XLEN masks, alignment
    bits in mtvec, etc.

  - Enum list: `"0,1,3"` — only the listed values are legal. Hardware
    coerces by "nearest legal value ≤ requested, or the minimum listed
    if the request is below all of them." This matches the RISC-V spec's
    permissive allowance for WARL enum implementations.

Arbitrary Python-callback legalization is out of scope for v1.
"""

from systemrdl.component import Field
from systemrdl.udp import UDPDefinition


class RiscvWarl(UDPDefinition):
    name = "riscv_warl"
    valid_components = {Field}
    valid_type = str

    def validate(self, node, value):
        s = value.strip()
        # Bitmask form: single literal starting with 0x / 0b / digits
        if s.startswith(("0x", "0X", "0b", "0B")) or (
            s and s[0].isdigit() and "," not in s
        ):
            try:
                int(s, 0)
            except ValueError:
                self.msg.error(
                    f"riscv_warl bitmask {value!r} is not a valid integer literal",
                    self.get_src_ref(node),
                )
            return
        # Enum list form: comma-separated integer literals
        if "," in s:
            for part in s.split(","):
                try:
                    int(part.strip(), 0)
                except ValueError:
                    self.msg.error(
                        f"riscv_warl enum list entry {part!r} is not a valid "
                        f"integer literal (full value: {value!r})",
                        self.get_src_ref(node),
                    )
            return
        self.msg.error(
            f"riscv_warl {value!r} must be either a bitmask literal "
            f"(e.g. '0x1F') or a comma-separated enum list (e.g. '0,1,3')",
            self.get_src_ref(node),
        )


def parse_warl(value: str) -> tuple[str, object]:
    """Return ('mask', int) or ('enum', [int, ...]) for a validated value."""
    s = value.strip()
    if "," in s:
        return ("enum", [int(p.strip(), 0) for p in s.split(",")])
    return ("mask", int(s, 0))
