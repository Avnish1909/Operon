"""Benchmark scenarios for the operator, with ground-truth checks.

Each scenario is a realistic, under-specified request against the sandbox company,
plus: injected faults, a scripted human (answers and approval decisions), and
checks that read the systems' databases and files directly. The checks never look
at what the operator *says* it did — only at what actually changed.
"""

from .catalog import SCENARIOS, Check, Scenario, get

__all__ = ["SCENARIOS", "Check", "Scenario", "get"]
