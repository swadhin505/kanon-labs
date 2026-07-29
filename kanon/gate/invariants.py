"""Policy invariants: the rules a twin cannot enforce but a run must obey.

The twin enforces what is *possible* (you cannot pay an unapproved claim). An
invariant enforces what is *allowed* (you may not pay past the plan's cap; you
may not file a claim without checking coverage first). That second category is
where the real incidents live -- Air Canada inventing a refund policy, Replit
dropping a database despite an all-caps prohibition -- and it is checkable here
precisely because the twin owns its own state.

Predicates are plain Python, deliberately. Ordering and cross-referencing over a
trajectory ("no payment without a prior approval *for that claim*") is a few
lines of Python and awkward-to-impossible in a rule DSL. CEL stays the
documented upgrade path for the day non-engineers author these as data.

Rules live with the domain they describe, not in this package -- see
`data/health-insurance/invariants.py`. This module is only the registry.
"""

from __future__ import annotations

import importlib.util
import sys
from collections.abc import Callable
from dataclasses import dataclass
from pathlib import Path

from kanon.gate.trajectory import Trajectory
from kanon.twin.store import State


@dataclass(frozen=True)
class Violation:
    """One breach of one rule. `step` is the trajectory position to blame."""

    rule: str
    message: str
    step: int | None = None

    def render(self, trajectory: Trajectory | None = None) -> str:
        line = f"{self.rule}: {self.message}"
        if self.step is not None and trajectory is not None:
            line += f"\n    {trajectory.describe(self.step)}"
        return line


#: A check returns every violation it finds. An empty list means it passed.
Check = Callable[[State, Trajectory], list[Violation]]


@dataclass(frozen=True)
class Invariant:
    name: str
    description: str
    #: Id of the policy this was derived from, so a failure can be traced back
    #: to the sentence in the customer's rulebook that motivated it.
    policy: str | None
    check: Check


_REGISTRY: dict[str, Invariant] = {}
_LOADED: dict[Path, list[str]] = {}


def invariant(
    name: str, *, description: str, policy: str | None = None
) -> Callable[[Check], Check]:
    """Register a predicate as a named invariant."""

    def register(check: Check) -> Check:
        if name in _REGISTRY:
            raise ValueError(f"invariant {name!r} is already registered")
        _REGISTRY[name] = Invariant(name, description, policy, check)
        return check

    return register


def registered() -> dict[str, Invariant]:
    return dict(_REGISTRY)


def load(path: str | Path) -> list[Invariant]:
    """Import a domain's invariant module by path; return what it registered.

    Loading the same file twice is a no-op rather than a duplicate-name error,
    so tests and repeated runs behave the same.
    """
    path = Path(path).resolve()
    if path in _LOADED:
        return [_REGISTRY[name] for name in _LOADED[path]]

    module_name = f"kanon_invariants_{path.stem}_{abs(hash(str(path)))}"
    spec = importlib.util.spec_from_file_location(module_name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load invariants from {path}")

    before = set(_REGISTRY)
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)

    added = [name for name in _REGISTRY if name not in before]
    _LOADED[path] = added
    return [_REGISTRY[name] for name in added]


def check(state: State, trajectory: Trajectory, names: list[str] | None = None) -> list[Violation]:
    """Run invariants against a finished run. Empty result means clean.

    Unknown names raise rather than being skipped: a rule that silently stops
    being checked is worse than no rule.
    """
    selected = list(_REGISTRY) if names is None else names
    unknown = [name for name in selected if name not in _REGISTRY]
    if unknown:
        raise KeyError(f"no such invariant: {', '.join(sorted(unknown))}")

    violations: list[Violation] = []
    for name in selected:
        violations.extend(_REGISTRY[name].check(state, trajectory))
    return violations
