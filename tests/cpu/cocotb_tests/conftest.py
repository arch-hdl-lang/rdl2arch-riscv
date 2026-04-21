"""Don't let pytest collect cocotb test modules as regular pytest tests.

They use `@cocotb.test()` and are run inside the simulator by
`cocotb_tools.runner.test(...)` from `tests/cpu/test_timer_isr.py`
(and friends). Collecting them as pytest would fail on the missing
`dut` fixture.
"""

collect_ignore_glob = ["test_*.py"]
