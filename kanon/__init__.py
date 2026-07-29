"""Kanon Labs: compile a spec into a deterministic twin, gate on it in CI.

Two time zones, visible in the tree:

    kanon/twin/   run time -- deterministic. No LLM, no clock, no RNG.
    kanon/gate/   run time -- deterministic scoring. Same rule.
    kanon/compile/  build time -- LLM allowed. Emits data, never runs in a test.
"""
