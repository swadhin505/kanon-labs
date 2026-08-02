"""Turn policy output into safe, human-reviewable invariant stubs."""

from __future__ import annotations

import keyword
import re
from pathlib import Path

import yaml
from pydantic import AliasChoices, BaseModel, ConfigDict, Field


class Policy(BaseModel):
    model_config = ConfigDict(extra="forbid")

    id: str
    name: str | None = None
    violation_condition: str = Field(
        validation_alias=AliasChoices("violation_condition", "violation")
    )


def load_policies(path: str | Path) -> list[Policy]:
    raw = yaml.safe_load(Path(path).read_text(encoding="utf-8")) or []
    if isinstance(raw, dict):
        raw = raw.get("policies")
    if not isinstance(raw, list):
        raise ValueError("policies must be a YAML list or a {policies: [...]} object")
    policies = [Policy.model_validate(item) for item in raw]
    names = [_identifier(policy) for policy in policies]
    duplicates = sorted({name for name in names if names.count(name) > 1})
    if duplicates:
        raise ValueError(f"policy names produce duplicate Python identifiers: {duplicates}")
    return policies


def render_stubs(policies: list[Policy]) -> str:
    lines = [
        '"""GENERATED DRAFTS. Review and move finished checks to invariants.py."""',
        "",
        "from kanon.gate.invariants import Violation, invariant",
        "from kanon.gate.trajectory import Trajectory",
        "from kanon.twin.store import State",
        "",
    ]
    for policy in policies:
        name = _identifier(policy)
        description = policy.violation_condition.strip()
        lines += [
            f"@invariant({name!r}, policy={policy.id!r}, description={description!r})",
            f"def {name}(state: State, trajectory: Trajectory) -> list[Violation]:",
            "    raise NotImplementedError(",
            f"        {'Review ' + policy.id + ' and implement: ' + description!r}",
            "    )",
            "",
        ]
    return "\n".join(lines)


def write_stubs(source: str | Path, output: str | Path) -> Path:
    output = Path(output)
    if output.exists():
        raise FileExistsError(f"refusing to overwrite review file {output}")
    output.write_text(render_stubs(load_policies(source)), encoding="utf-8")
    return output


def _identifier(policy: Policy) -> str:
    name = re.sub(r"\W+", "_", policy.name or policy.id).strip("_").lower()
    if not name or name[0].isdigit() or keyword.iskeyword(name):
        name = f"policy_{name}"
    return name
