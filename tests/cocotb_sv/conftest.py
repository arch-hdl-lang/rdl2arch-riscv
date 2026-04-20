"""Prevent pytest from collecting cocotb test modules as pytest tests.

These modules use `@cocotb.test()` and are invoked by the Verilator runner
via `test_verilator.py`. They are not self-contained pytest tests.
"""

collect_ignore_glob = ["cocotb_tests/test_*.py"]
