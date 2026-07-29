"""Inferring state transitions -- the one thing no spec contains.

This is the only genuine guess in the product, so it is fenced on both sides:

**Before:** the *states* are not guessed. They come from the spec's own `status`
enum, which the deterministic pass already found. The LLM is only asked which
edges connect them, and which operation moves a record along which edge.

**After:** every claim is checked against the spec before it is kept. An invented
state, an unknown operation, a `sets_state` that is not in the enum -- all
rejected and reported, never merged. A wrong guess that survives validation is
still possible, which is what `fidelity.py` is for.

Output is a **merge patch**, written to its own layer (`pack.inferred.yaml`), so a
reviewer can diff it, edit over it, or delete it to reject every guess at once.
"""

from __future__ import annotations

import json
import os
from dataclasses import dataclass, field
from typing import Any

from kanon.twin.pack import Pack

DEFAULT_MODEL = "gpt-5-mini"

_PROMPT = """You are describing the lifecycle of a `{resource}` in the {api} API.

Its status field can hold exactly these values, taken from the API's own schema:
{states}

These are the operations that write to a `{resource}`:
{operations}

Return JSON with exactly two keys:

  "transitions": an object mapping each status to the list of statuses a record
                 can legally move to next. Terminal statuses map to an empty
                 list. Every status above must appear as a key.
  "sets_state":  an object mapping each operation name above to the single status
                 that operation moves the record into. Omit an operation only if it
                 genuinely never changes the status. **The operation that creates a
                 {resource} must be included**, mapped to the status a brand-new
                 record starts in -- otherwise a new record has no status at all
                 and every later transition is impossible.

Use only the status values listed. Do not invent statuses or operations. Base the
lifecycle on how this API actually behaves, and prefer refusing an edge to
inventing one: a missing edge shows up as a test failure a human can see, an
invented edge silently lets an agent do something the real API forbids.
"""


@dataclass
class Inference:
    """A merge patch plus everything that was thrown away, and why."""

    patch: dict[str, Any] = field(default_factory=dict)
    #: Claims rejected by validation. Reported, never silently dropped.
    rejected: list[str] = field(default_factory=list)
    model_calls: int = 0

    @property
    def resources(self) -> list[str]:
        return sorted((self.patch.get("resources") or {}).keys())


def infer_transitions(
    pack: Pack,
    states: dict[str, list[str]],
    api_name: str = "",
    model: str = DEFAULT_MODEL,
    client: Any = None,
) -> Inference:
    """Ask a model for the edges between states the spec already gave us."""
    inference = Inference()
    client = client or _openai()

    for resource in sorted(states):
        allowed = list(states[resource])
        writers = _writers(pack, resource)
        if not allowed or not writers:
            continue

        raw = _ask(client, model, api_name or pack.name, resource, allowed, writers)
        inference.model_calls += 1
        _absorb(inference, pack, resource, allowed, writers, raw)

    # Drop empty containers, so "nothing survived" reads as a falsy patch rather
    # than writing an inferred layer with nothing in it.
    inference.patch = {key: value for key, value in inference.patch.items() if value}
    return inference


def _writers(pack: Pack, resource: str) -> dict[str, str]:
    """Operations that write to `resource`, with their descriptions."""
    return {
        name: route.description or f"{route.verb} a {resource}"
        for name, route in sorted(pack.routes.items())
        if route.resource == resource and route.verb in ("create", "update")
    }


def _ask(
    client: Any,
    model: str,
    api: str,
    resource: str,
    states: list[str],
    writers: dict[str, str],
) -> dict[str, Any]:
    prompt = _PROMPT.format(
        resource=resource,
        api=api,
        states="\n".join(f"  - {s}" for s in states),
        operations="\n".join(f"  - {name}: {text}" for name, text in writers.items()),
    )
    response = client.chat.completions.create(
        model=model,
        messages=[{"role": "user", "content": prompt}],
        response_format={"type": "json_object"},
    )
    content = response.choices[0].message.content or "{}"
    try:
        parsed = json.loads(content)
    except json.JSONDecodeError:
        return {}
    return parsed if isinstance(parsed, dict) else {}


def _absorb(
    inference: Inference,
    pack: Pack,
    resource: str,
    allowed: list[str],
    writers: dict[str, str],
    raw: dict[str, Any],
) -> None:
    """Validate the model's answer against the spec, keeping only what holds."""
    permitted = set(allowed)

    transitions: dict[str, list[str]] = {}
    for state, targets in (raw.get("transitions") or {}).items():
        if state not in permitted:
            inference.rejected.append(f"{resource}: invented state {state!r}")
            continue
        if not isinstance(targets, list):
            inference.rejected.append(f"{resource}: {state!r} targets are not a list")
            continue
        kept = []
        for target in targets:
            if target in permitted:
                kept.append(str(target))
            else:
                inference.rejected.append(f"{resource}: {state!r} -> invented state {target!r}")
        transitions[str(state)] = kept

    # Every state the spec declares must be a key, or the engine reads an absent
    # one as terminal and silently forbids every move out of it.
    for state in allowed:
        transitions.setdefault(state, [])

    if not any(transitions.values()):
        inference.rejected.append(f"{resource}: no usable transitions returned")
        return

    resources = inference.patch.setdefault("resources", {})
    resources[resource] = {"transitions": transitions}

    routes = inference.patch.setdefault("routes", {})
    kept: dict[str, str] = {}
    for operation, state in (raw.get("sets_state") or {}).items():
        if operation not in writers:
            inference.rejected.append(f"{resource}: unknown operation {operation!r}")
        elif state not in permitted:
            inference.rejected.append(f"{resource}: {operation} sets invented state {state!r}")
        else:
            routes[str(operation)] = {"sets_state": str(state)}
            kept[str(operation)] = str(state)

    for problem in _unusable(pack, resource, writers, kept):
        inference.rejected.append(f"{resource}: {problem} -- dropped")
        resources.pop(resource, None)
        for operation in kept:
            routes.pop(operation, None)
        return


def _unusable(
    pack: Pack, resource: str, writers: dict[str, str], kept: dict[str, str]
) -> list[str]:
    """Reasons a state machine would be enforced-in-name-only, so must not ship."""
    problems = []
    if not kept:
        # Transitions are only checked on routes that declare `sets_state`. A pack
        # with a full state machine and nothing driving it enforces nothing, which
        # reads as coverage while checking nothing.
        problems.append("transitions inferred but no operation sets a state")

    creates = [name for name in writers if pack.routes[name].verb == "create"]
    if creates and not any(name in kept for name in creates):
        # Found by the fidelity gate on real Stripe: the model mapped `capture` but
        # not `PostCharges`, so every charge was born with no status and the very
        # first transition failed.
        problems.append(
            f"no create operation sets an initial state ({', '.join(sorted(creates))}), "
            "so new records would have no status and every transition would fail"
        )
    return problems


def _openai() -> Any:
    from openai import OpenAI  # imported lazily so the twin runs without it

    return OpenAI(api_key=os.environ.get("MODEL_API_KEY") or os.environ.get("OPENAI_API_KEY"))
