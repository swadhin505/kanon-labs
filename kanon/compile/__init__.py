"""BUILD TIME. The only place an LLM is allowed -- and the deterministic parts
deliberately don't use one.

`openapi.py` is pure parsing: resources, routes, field types, descriptions, all
of it read out of the schema. The LLM pass that infers state transitions is not
built yet; until it is, a compiled pack has empty `transitions` and the twin
enforces no state machine.
"""

from kanon.compile.fidelity import Fidelity, Mismatch, Trace, check_fidelity, load_traces
from kanon.compile.openapi import Compiled, Uncovered, compile_spec
from kanon.compile.transitions import Inference, infer_transitions

__all__ = [
    "Compiled",
    "Fidelity",
    "Inference",
    "Mismatch",
    "Trace",
    "Uncovered",
    "check_fidelity",
    "compile_spec",
    "infer_transitions",
    "load_traces",
]
