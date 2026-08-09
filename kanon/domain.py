"""Loading one customer's world: their twin, their rules, their scenarios.

A domain is a directory. Everything industry-specific lives in it, and nothing
industry-specific lives anywhere else:

    pack.generated.yaml  read out of the spec by the compiler -- never hand-edit
    pack.inferred.yaml   guessed by an LLM -- review, or delete to reject
    pack.yaml            written by a human -- always wins
    invariants.py        the policy rules (optional)
    stories.yaml         the scenarios (optional)
    agents.py            the agents under test, as `AGENTS = {name: agent}` (optional)

The pack layers are merged in that order. The file boundary is the provenance
boundary, so neither machine step can touch the file your edits live in.

`data/health-insurance/` and `data/bank/` are two of these, and the product has
never been changed for either.
"""

from __future__ import annotations

import importlib.util
import sys
from dataclasses import dataclass, field
from pathlib import Path
from typing import Any

from kanon.gate import invariants
from kanon.gate.runner import Agent
from kanon.gate.story import Story, load_stories
from kanon.twin import Pack, Twin, TwinError


@dataclass(frozen=True)
class Domain:
    name: str
    path: Path
    pack: Pack
    stories: list[Story] = field(default_factory=list)
    agents: dict[str, Agent] = field(default_factory=dict)

    @classmethod
    def load(cls, directory: str | Path) -> Domain:
        path = Path(directory).resolve()
        if not path.is_dir():
            raise NotADirectoryError(f"{path} is not a domain directory")

        pack = Pack.from_layers(
            path / "pack.generated.yaml", path / "pack.inferred.yaml", path / "pack.yaml"
        )

        if (path / "invariants.py").exists():
            invariants.load(path / "invariants.py")

        stories = load_stories(path / "stories.yaml") if (path / "stories.yaml").exists() else []
        _validate_stories(stories, pack)
        agents = _load_agents(path / "agents.py") if (path / "agents.py").exists() else {}

        return cls(pack.name, path, pack, stories, agents)

    def agent(self, name: str) -> Agent:
        if name not in self.agents:
            known = ", ".join(sorted(self.agents)) or "none"
            raise KeyError(f"no agent {name!r} in {self.path} (available: {known})")
        return self.agents[name]


def _load_agents(path: Path) -> dict[str, Agent]:
    """Import `agents.py` and return its `AGENTS` mapping."""
    module = _import_file(path)
    agents: Any = getattr(module, "AGENTS", None)
    if not isinstance(agents, dict):
        raise ValueError(f"{path} must define AGENTS as a dict of name -> agent")
    return agents


def _validate_stories(stories: list[Story], pack: Pack) -> None:
    """Reject story typos and invalid per-story worlds at domain load time."""
    twin = Twin(pack)
    known = set(pack.routes)
    for story in stories:
        operations = {
            *story.must_call,
            *(fault.operation for fault in story.faults),
            *(operation for expected in story.calls for operation in expected.operations),
            *(turn.after_call for turn in story.user_turns if turn.after_call),
            *(
                confirmation.operation
                for turn in story.user_turns
                for confirmation in turn.confirms
            ),
        }
        unknown = sorted(operations - known)
        if unknown:
            raise ValueError(f"story {story.id!r} names unknown operations: {', '.join(unknown)}")
        try:
            twin.reset(story.given, [fault.model_dump() for fault in story.faults])
        except TwinError as exc:
            raise ValueError(f"story {story.id!r}: {exc.message}") from None


def _import_file(path: Path):
    """Import a module by file path.

    Six lines duplicated from `invariants.load`, which keeps its own copy so
    that module stays importable without pulling this one in -- cheaper than a
    circular import or a package just to hold one helper.
    """
    name = f"kanon_domain_{path.parent.name}_{path.stem}"
    if name in sys.modules:
        return sys.modules[name]

    spec = importlib.util.spec_from_file_location(name, path)
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot import {path}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[name] = module
    spec.loader.exec_module(module)
    return module
