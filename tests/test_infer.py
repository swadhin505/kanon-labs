"""Checks for the inferred layer and the gate that keeps it honest.

The inference test uses a fake model, so the validation logic is tested offline:
the point is not that a model gives good answers, it is that a bad answer cannot
reach the pack.
"""

from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace
from typing import Any

import pytest

from kanon.compile import check_fidelity, infer_transitions, load_traces
from kanon.compile.fidelity import Trace, TraceCall
from kanon.twin import Pack
from kanon.twin.pack import merge_patch

SPEC_STATES = {"charge": ["pending", "succeeded", "failed"]}

GENERATED = {
    "name": "payments",
    "resources": {
        "charge": {
            "id_field": "id",
            "state_field": "status",
            "fields": {"id": "string", "amount": "integer", "status": "string"},
        }
    },
    "routes": {
        "PostCharges": {"resource": "charge", "verb": "create", "requires": ["amount"]},
        "PostChargesChargeCapture": {"resource": "charge", "verb": "update", "id_param": "charge"},
        "PostChargesChargeFail": {"resource": "charge", "verb": "update", "id_param": "charge"},
        "GetChargesCharge": {"resource": "charge", "verb": "read", "id_param": "charge"},
    },
}


def fake_client(payload: dict[str, Any]):
    def create(**_kwargs):
        message = SimpleNamespace(content=json.dumps(payload))
        return SimpleNamespace(choices=[SimpleNamespace(message=message)])

    return SimpleNamespace(chat=SimpleNamespace(completions=SimpleNamespace(create=create)))


GOOD_ANSWER = {
    "transitions": {
        "pending": ["succeeded", "failed"],
        "succeeded": [],
        "failed": [],
    },
    "sets_state": {
        "PostCharges": "pending",
        "PostChargesChargeCapture": "succeeded",
        "PostChargesChargeFail": "failed",
    },
}


# --- inference is fenced by the spec ------------------------------------


def test_a_good_answer_becomes_a_patch_that_validates() -> None:
    pack = Pack.model_validate(GENERATED)
    inference = infer_transitions(pack, SPEC_STATES, client=fake_client(GOOD_ANSWER))

    assert inference.rejected == []
    merged = Pack.model_validate(merge_patch(GENERATED, inference.patch))
    assert merged.resources["charge"].transitions["pending"] == ["succeeded", "failed"]
    assert merged.routes["PostChargesChargeCapture"].sets_state == "succeeded"
    assert merged.routes["GetChargesCharge"].sets_state is None, "reads never set state"


def test_invented_states_are_rejected_not_merged() -> None:
    pack = Pack.model_validate(GENERATED)
    inference = infer_transitions(
        pack,
        SPEC_STATES,
        client=fake_client(
            {
                "transitions": {
                    "pending": ["succeeded", "refunded"],  # refunded is not in the spec
                    "cancelled": ["pending"],  # nor is cancelled
                    "succeeded": [],
                    "failed": [],
                },
                "sets_state": {"PostCharges": "pending"},
            }
        ),
    )

    transitions = inference.patch["resources"]["charge"]["transitions"]
    assert transitions["pending"] == ["succeeded"], "the invented target is dropped"
    assert "cancelled" not in transitions
    assert any("invented state 'refunded'" in r for r in inference.rejected)
    assert any("invented state 'cancelled'" in r for r in inference.rejected)


def test_unknown_operations_and_states_are_rejected() -> None:
    pack = Pack.model_validate(GENERATED)
    inference = infer_transitions(
        pack,
        SPEC_STATES,
        client=fake_client(
            {
                "transitions": {"pending": ["succeeded"], "succeeded": [], "failed": []},
                "sets_state": {
                    "PostChargesChargeRefund": "succeeded",  # no such operation
                    "PostChargesChargeCapture": "settled",  # no such state
                    "GetChargesCharge": "pending",  # not a writer
                },
            }
        ),
    )

    assert inference.patch.get("routes", {}) == {}
    assert any("unknown operation 'PostChargesChargeRefund'" in r for r in inference.rejected)
    assert any("invented state 'settled'" in r for r in inference.rejected)
    assert any("no operation sets a state" in r for r in inference.rejected), (
        "nothing drives the machine, so keeping the transitions would be theatre"
    )
    assert inference.patch == {}


def test_every_declared_state_becomes_a_key() -> None:
    """A state the model forgets would otherwise read as terminal, silently
    forbidding every move out of it."""
    pack = Pack.model_validate(GENERATED)
    inference = infer_transitions(
        pack,
        SPEC_STATES,
        client=fake_client(
            {
                "transitions": {"pending": ["succeeded"]},
                "sets_state": {"PostCharges": "pending", "PostChargesChargeCapture": "succeeded"},
            }
        ),
    )
    transitions = inference.patch["resources"]["charge"]["transitions"]
    assert set(transitions) == {"pending", "succeeded", "failed"}


def test_a_machine_with_no_initial_state_is_dropped() -> None:
    """Found by the fidelity gate on the real Stripe spec: the model mapped
    `capture` but not `PostCharges`, so every charge was born with no status and
    the very first transition failed. A machine nothing can enter is not a
    machine."""
    pack = Pack.model_validate(GENERATED)
    inference = infer_transitions(
        pack,
        SPEC_STATES,
        client=fake_client(
            {
                "transitions": {"pending": ["succeeded"], "succeeded": [], "failed": []},
                "sets_state": {"PostChargesChargeCapture": "succeeded"},  # no create
            }
        ),
    )

    assert inference.patch == {}, "shipped nothing rather than a machine nothing can enter"
    assert any("no create operation sets an initial state" in r for r in inference.rejected)


def test_a_useless_answer_is_dropped_entirely() -> None:
    pack = Pack.model_validate(GENERATED)
    inference = infer_transitions(pack, SPEC_STATES, client=fake_client({"transitions": {}}))
    assert inference.patch == {}
    assert any("no usable transitions" in r for r in inference.rejected)


# --- the fidelity gate ---------------------------------------------------


def inferred_pack() -> Pack:
    return Pack.model_validate(merge_patch(GENERATED, GOOD_ANSWER_PATCH))


GOOD_ANSWER_PATCH = {
    "resources": {"charge": {"transitions": GOOD_ANSWER["transitions"]}},
    "routes": {name: {"sets_state": state} for name, state in GOOD_ANSWER["sets_state"].items()},
}


def test_a_trace_the_twin_agrees_with_passes() -> None:
    traces = [
        Trace(
            name="capture after authorise",
            calls=[
                TraceCall(operation="PostCharges", args={"amount": 500}),
                TraceCall(operation="PostChargesChargeCapture", args={"charge": "0001"}),
            ],
        )
    ]
    result = check_fidelity(inferred_pack(), traces)
    assert result.ok, [m.describe() for m in result.mismatches]
    assert result.checked == 2


def test_a_double_capture_the_real_api_refuses_must_also_be_refused() -> None:
    """The dangerous direction: the twin being more permissive than reality."""
    traces = [
        Trace(
            name="capture twice",
            calls=[
                TraceCall(operation="PostCharges", args={"amount": 500}),
                TraceCall(operation="PostChargesChargeCapture", args={"charge": "0001"}),
                TraceCall(
                    operation="PostChargesChargeCapture",
                    args={"charge": "0001"},
                    outcome="refused",
                    error="invalid_transition",
                ),
            ],
        )
    ]
    assert check_fidelity(inferred_pack(), traces).ok


def test_a_too_strict_pack_is_caught() -> None:
    """The real API accepted it; a pack that forbids the edge is wrong."""
    too_strict = merge_patch(
        merge_patch(GENERATED, GOOD_ANSWER_PATCH),
        {"resources": {"charge": {"transitions": {"pending": [], "succeeded": [], "failed": []}}}},
    )
    traces = [
        Trace(
            name="capture after authorise",
            calls=[
                TraceCall(operation="PostCharges", args={"amount": 500}),
                TraceCall(operation="PostChargesChargeCapture", args={"charge": "0001"}),
            ],
        )
    ]
    result = check_fidelity(Pack.model_validate(too_strict), traces)
    assert not result.ok
    assert "real API accepted, twin refused" in result.mismatches[0].describe()


def test_a_too_permissive_pack_is_caught() -> None:
    permissive = merge_patch(
        GENERATED,
        {
            "resources": {
                "charge": {
                    "transitions": {
                        "pending": ["succeeded"],
                        "succeeded": ["succeeded"],  # re-capture allowed: wrong
                        "failed": [],
                    }
                }
            },
            "routes": {
                "PostCharges": {"sets_state": "pending"},
                "PostChargesChargeCapture": {"sets_state": "succeeded"},
            },
        },
    )
    traces = [
        Trace(
            name="capture twice",
            calls=[
                TraceCall(operation="PostCharges", args={"amount": 500}),
                TraceCall(operation="PostChargesChargeCapture", args={"charge": "0001"}),
                TraceCall(
                    operation="PostChargesChargeCapture", args={"charge": "0001"}, outcome="refused"
                ),
            ],
        )
    ]
    result = check_fidelity(Pack.model_validate(permissive), traces)
    assert not result.ok
    assert "real API refused, twin accepted" in result.mismatches[0].describe()


def test_no_traces_is_reported_as_unverified_not_as_a_pass() -> None:
    result = check_fidelity(inferred_pack(), [])
    assert result.ok, "nothing disagreed"
    assert "unverified" in result.summary(), "but it must not read as verified either"


def test_traces_load_from_yaml(tmp_path: Path) -> None:
    path = tmp_path / "traces.yaml"
    path.write_text(
        "- name: authorise then capture\n"
        "  calls:\n"
        "    - {operation: PostCharges, args: {amount: 500}}\n"
        "    - {operation: PostChargesChargeCapture, args: {charge: '0001'}, outcome: refused}\n",
        encoding="utf-8",
    )
    traces = load_traces(path)
    assert traces[0].name == "authorise then capture"
    assert traces[0].calls[1].outcome == "refused"


def test_a_bad_trace_file_fails_loudly(tmp_path: Path) -> None:
    path = tmp_path / "traces.yaml"
    path.write_text("- name: x\n  calls: [{operation: A, outcome: maybe}]\n", encoding="utf-8")
    with pytest.raises(ValueError):
        load_traces(path)
