"""The gate's runnable check: a good agent passes every story, a broken one
fails the right ones for the right stated reason, and a story that cannot fail
is reported as invalid.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.domain import Domain
from kanon.gate.metrics import RunReport
from kanon.gate.runner import Call, NullAgent, Say, ScriptedAgent, play, run_all, run_story
from kanon.gate.scorer import diff, score
from kanon.gate.story import (
    CallExpectation,
    Change,
    Confirmation,
    Fault,
    Limits,
    Outcome,
    Story,
    UserTurn,
    load_stories,
)
from kanon.gate.trajectory import ToolCall, Trajectory
from kanon.twin import Pack, Twin

INSURANCE = Domain.load(Path(__file__).resolve().parents[1] / "data" / "health-insurance")
STORIES = {story.id: story for story in INSURANCE.stories}

# The same agent before and after a prompt change that dropped "check the annual
# cap" and "look the member up first". Both live in the domain, not here, so the
# CLI demo and these tests exercise exactly the same agents.
GOOD = INSURANCE.agent("good")
BROKEN = INSURANCE.agent("broken")


@pytest.fixture
def twin() -> Twin:
    return Twin(INSURANCE.pack)


# --- the diff ------------------------------------------------------------


def test_diff_reports_creates_changes_and_deletes(twin: Twin) -> None:
    seeded = twin.state()
    twin.call("submit_claim", {"member_id": "MEM-0001", "service_code": "D0120", "amount": 90})
    twin.call("lapse_member", {"member_id": "MEM-0001"})

    deltas = {(d.resource, d.id): d for d in diff(seeded, twin.state())}
    assert deltas[("claim", "CLM-0002")].op == "created"
    assert deltas[("member", "MEM-0001")].fields["status"] == ("active", "lapsed")
    assert "status: 'active' -> 'lapsed'" in deltas[("member", "MEM-0001")].describe()


def test_unexpected_changes_fail_by_default(twin: Twin) -> None:
    """Nothing is tolerated unless the story says so -- collateral damage is the
    class of bug a hash comparison of the whole state cannot describe."""
    story = Story(id="t", intent="t", persona="t", goal="t")
    seeded = twin.state()
    twin.call("lapse_member", {"member_id": "MEM-0001"})

    result = score(story, seeded, twin.state(), Trajectory())
    assert not result.state_ok
    assert result.reasons == [
        "unexpected change: member MEM-0001 changed (status: 'active' -> 'lapsed')"
    ]


def test_allowed_changes_are_tolerated_but_not_required(twin: Twin) -> None:
    story = Story(
        id="t", intent="t", persona="t", goal="t", allow=[Change(resource="member", op="changed")]
    )
    seeded = twin.state()
    twin.call("lapse_member", {"member_id": "MEM-0001"})

    assert score(story, seeded, twin.state(), Trajectory()).passed
    # ...and the story still passes when the tolerated change does not happen.
    assert score(story, seeded, seeded, Trajectory()).passed


# --- running stories -----------------------------------------------------


def test_a_good_agent_passes_every_story(twin: Twin) -> None:
    results = run_all(twin, list(STORIES.values()), GOOD, trials=2)

    failures = {r.story.id: r.trials[0].score.reasons for r in results if r.successes < 2}
    assert failures == {}
    assert not any(r.trivially_passed for r in results)


def test_the_environment_is_deterministic_so_trials_agree(twin: Twin) -> None:
    result = run_story(twin, STORIES["hi-001"], GOOD, trials=5)
    assert result.successes == 5, "a scripted agent against a deterministic twin cannot vary"


def test_adversarial_user_pushback_happens_before_the_agent_decides(twin: Twin) -> None:
    trial = play(twin, STORIES["hi-002"], GOOD)
    user_step = next(
        step for step, message in trial.trajectory.messages() if message.role == "user"
    )
    approval_step = trial.trajectory.calls("approve_claim")[0][0]
    assert user_step < approval_step
    assert "approve the full 40000" in trial.trajectory.describe(user_step)


def test_confirmation_waits_for_its_explicit_tool_marker(twin: Twin) -> None:
    actions = [Say("Let me check that first."), *GOOD.scripts["hi-005"]]
    trial = play(twin, STORIES["hi-005"], ScriptedAgent("narrating", {"hi-005": actions}))

    user_step = next(
        step for step, message in trial.trajectory.messages() if message.role == "user"
    )
    approval_step = trial.trajectory.calls("approve_claim")[0][0]
    payment_step = trial.trajectory.calls("pay_claim")[0][0]
    assert approval_step < user_step < payment_step
    assert trial.score.passed, trial.score.reasons


def test_a_broken_agent_fails_for_the_stated_reason(twin: Twin) -> None:
    results = {r.story.id: r for r in run_all(twin, list(STORIES.values()), BROKEN)}

    # hi-001: filed without looking the member up first.
    reasons = results["hi-001"].trials[0].score.reasons
    assert results["hi-001"].successes == 0
    assert "called get_member 0x, expected at least 1x" in reasons
    assert any("without reading their record first" in r for r in reasons)

    # hi-002: approved past the annual cap.
    over_cap = results["hi-002"].trials[0].score
    assert not over_cap.invariants_ok
    assert any("annual cap of 25000" in r for r in over_cap.reasons)
    assert any("approve_claim" in r for r in over_cap.reasons), "the failing step is cited"

    # hi-003: wrote something when nothing should have changed.
    assert not results["hi-003"].trials[0].score.state_ok
    assert any(
        "unexpected change: claim CLM-0002 created" in r
        for r in results["hi-003"].trials[0].score.reasons
    )

    # hi-004 is read-only and the broken agent still does it right.
    assert results["hi-004"].successes == 1

    # hi-005: paid first and only obtained confirmation afterwards.
    reasons = results["hi-005"].trials[0].score.reasons
    assert any("before user confirmation" in reason for reason in reasons)


def test_saved_report_keeps_drill_in_evidence(twin: Twin, tmp_path: Path) -> None:
    results = run_all(twin, [STORIES["hi-002"]], BROKEN)
    report = RunReport.from_results("health-insurance", "broken", results, twin.uncovered)

    path = report.save(tmp_path / "run.json")
    loaded = RunReport.load(path)
    trial = loaded.stories[0].trial_details[0]

    assert not trial.passed
    assert trial.events[0].operation == "get_member"
    assert trial.events[0].deterministic is True
    assert trial.changes[0].resource == "claim"
    cap = next(item for item in trial.invariants if item.name == "payout_within_annual_cap")
    assert not cap.passed
    assert cap.violations[0].step == 6


def test_a_run_that_never_stops_is_a_failure_not_a_hang(twin: Twin) -> None:
    class Forever:
        name = "forever"

        def start(self, story: Story) -> None:
            return None

        def next_action(self, trajectory: Trajectory) -> Call:
            return Call("get_member", {"member_id": "MEM-0001"})

    trial = play(twin, STORIES["hi-001"], Forever(), max_steps=5)
    assert len(trial.trajectory.events) == 6, "5 calls plus the gave-up note"
    assert not trial.score.passed


# --- the no-op baseline --------------------------------------------------


def test_a_story_that_cannot_fail_is_reported(twin: Twin) -> None:
    """hi-003 expects no state change. Strip its call constraint and an agent that
    does nothing passes it -- so the story proves nothing. tau2 ships real tasks
    with exactly this hole; we refuse to count them."""
    hollow = STORIES["hi-003"].model_copy(update={"calls": []})

    assert play(twin, hollow, NullAgent()).score.passed
    assert run_story(twin, hollow, GOOD).trivially_passed
    assert not run_story(twin, STORIES["hi-003"], GOOD).trivially_passed


# --- story loading -------------------------------------------------------


def test_stories_carry_their_slice() -> None:
    assert STORIES["hi-001"].slice == (
        ("intent", "file_claim"),
        ("policy", "HI-P1"),
        ("persona", "cooperative"),
        ("channel", "chat"),
        ("locale", "en"),
    )
    assert dict(STORIES["hi-004"].slice)["policy"] == "-"
    assert STORIES["hi-005"].user_turns[0].confirms == [
        Confirmation(operation="pay_claim", id_from="claim_id")
    ]
    assert STORIES["hi-005"].user_turns[0].after_call == "approve_claim"


def test_confirmation_metadata_cannot_be_positional() -> None:
    with pytest.raises(ValueError, match="requires after_call"):
        UserTurn(
            content="yes",
            confirms=[Confirmation(operation="pay_claim", id_from="claim_id")],
        )


def test_confirmation_target_comes_from_the_triggering_call(twin: Twin) -> None:
    recovering = ScriptedAgent(
        "recovers",
        {
            "hi-005": [
                Call("get_member", {"member_id": "MEM-0001"}),
                Call("get_plan", {"plan_id": "PLN-0002"}),
                Call("list_claims", {"member_id": "MEM-0001"}),
                Call(
                    "submit_claim",
                    {"member_id": "MEM-0001", "service_code": "D2750", "amount": 800},
                ),
                Call("deny_claim", {"claim_id": "CLM-0002", "reason": "wrong code"}),
                Call(
                    "submit_claim",
                    {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800},
                ),
                Call("review_claim", {"claim_id": "CLM-0003"}),
                Call("approve_claim", {"claim_id": "CLM-0003", "approved_amount": 720}),
                Say("Approved for 720. Pay it?"),
                Call("pay_claim", {"claim_id": "CLM-0003"}),
            ]
        },
    )
    story = STORIES["hi-005"].model_copy(
        update={"allow": [Change(resource="claim", op="created", fields={"status": "denied"})]}
    )

    trial = play(twin, story, recovering)

    confirmations = [
        message.confirms
        for _, message in trial.trajectory.messages()
        if message.role == "user"
    ]
    assert confirmations == [("pay_claim:CLM-0003",)]
    assert trial.score.passed, trial.score.reasons


def test_a_waiting_authored_turn_does_not_silence_the_adaptive_user(twin: Twin) -> None:
    class Adaptive:
        findings = []

        def __init__(self) -> None:
            self.calls = 0

        def start(self, story: Story) -> None:
            return None

        def reply(self, trajectory: Trajectory) -> UserTurn:
            self.calls += 1
            return UserTurn(content="Here is the information you asked for.")

    story = Story(
        id="waiting",
        intent="ask",
        persona="cooperative",
        goal="answer a question",
        user_turns=[UserTurn(content="required pushback", after_call="get_member")],
    )
    user = Adaptive()

    trial = play(twin, story, ScriptedAgent("asks", {"waiting": [Say("Which member?")]}), user=user)

    assert user.calls == 1
    assert any(
        message.role == "user" and message.content.startswith("Here is")
        for _, message in trial.trajectory.messages()
    )
    assert not trial.score.interaction_ok
    assert any("required authored user turn never ran" in reason for reason in trial.score.reasons)


def test_one_tool_call_cannot_trigger_two_authored_turns(twin: Twin) -> None:
    story = Story(
        id="two-turns",
        intent="ask",
        persona="cooperative",
        goal="ask twice",
        user_turns=[
            UserTurn(content="first", after_call="get_member"),
            UserTurn(content="second", after_call="get_member"),
        ],
    )
    agent = ScriptedAgent(
        "talks",
        {
            "two-turns": [
                Call("get_member", {"member_id": "MEM-0001"}),
                Say("one"),
                Say("two"),
            ]
        },
    )

    trial = play(twin, story, agent)

    users = [
        message.content for _, message in trial.trajectory.messages() if message.role == "user"
    ]
    assert users == ["first"]
    assert not trial.score.interaction_ok


def test_an_unfired_turn_does_not_block_a_later_eligible_turn(twin: Twin) -> None:
    story = Story(
        id="out-of-order",
        intent="ask",
        persona="cooperative",
        goal="reply when relevant",
        user_turns=[
            UserTurn(content="plan reply", after_call="get_plan"),
            UserTurn(content="member reply", after_call="get_member"),
        ],
    )
    agent = ScriptedAgent(
        "member-only",
        {
            "out-of-order": [
                Call("get_member", {"member_id": "MEM-0001"}),
                Say("I found your membership."),
            ]
        },
    )

    trial = play(twin, story, agent)

    users = [
        message.content for _, message in trial.trajectory.messages() if message.role == "user"
    ]
    assert users == ["member reply"]
    assert not trial.score.interaction_ok, "the still-required plan reply remains visible"


def test_story_given_overlays_the_validated_starting_world(twin: Twin) -> None:
    story = Story(
        id="lapsed",
        intent="lookup",
        persona="cooperative",
        goal="check a lapsed member",
        given={"member": [{"member_id": "MEM-0001", "status": "lapsed"}]},
        calls=[CallExpectation(operation="get_member")],
    )
    agent = ScriptedAgent(
        "lookup", {"lapsed": [Call("get_member", {"member_id": "MEM-0001"})]}
    )

    trial = play(twin, story, agent)

    assert trial.before["member"]["MEM-0001"]["status"] == "lapsed"
    assert trial.score.passed, trial.score.reasons


def test_a_story_can_inject_a_deterministic_provider_failure(twin: Twin) -> None:
    story = Story(
        id="unavailable",
        intent="lookup",
        persona="cooperative",
        goal="handle an outage",
        faults=[Fault(operation="get_member", status=503, code="unavailable")],
        calls=[CallExpectation(operation="get_member", outcome="error")],
    )
    agent = ScriptedAgent(
        "tries", {"unavailable": [Call("get_member", {"member_id": "MEM-0001"})]}
    )

    trial = play(twin, story, agent)

    assert trial.trajectory.calls()[0][1].error == "unavailable"
    assert trial.score.passed, trial.score.reasons


def test_call_constraints_check_alternatives_arguments_and_counts() -> None:
    story = Story(
        id="calls",
        intent="pay",
        persona="cooperative",
        goal="pay once",
        calls=[
            CallExpectation(any_of=["get_member", "list_claims"], args={"member_id": "M-1"}),
            CallExpectation(operation="pay_claim", outcome="any", minimum=0, maximum=1),
        ],
    )
    trajectory = Trajectory(
        [
            ToolCall("list_claims", {"member_id": "M-1"}, []),
            ToolCall("pay_claim", {"claim_id": "C-1"}, error="timeout"),
            ToolCall("pay_claim", {"claim_id": "C-1"}, {}),
        ]
    )

    result = score(story, {}, {}, trajectory)

    assert not result.calls_ok
    assert "called pay_claim 2x, expected at most 1x" in result.reasons


def test_model_call_limits_are_enforced(twin: Twin) -> None:
    class Expensive:
        name = "expensive"
        calls_made = 0

        def start(self, story: Story) -> None:
            return None

        def next_action(self, trajectory: Trajectory):
            self.calls_made += 1
            return None

    story = Story(
        id="cost",
        intent="ask",
        persona="cooperative",
        goal="answer cheaply",
        limits=Limits(model_calls=0),
    )

    trial = play(twin, story, Expensive())

    assert not trial.score.calls_ok
    assert "used 1 model calls, limit is 0" in trial.score.reasons


def test_any_fully_matching_outcome_passes_and_is_reported(twin: Twin) -> None:
    seeded = twin.state()
    twin.call("lapse_member", {"member_id": "MEM-0001"})
    story = Story(
        id="outcomes",
        intent="resolve",
        persona="cooperative",
        goal="resolve safely",
        outcomes=[
            Outcome(
                name="filed",
                expect=[Change(resource="claim", op="created")],
            ),
            Outcome(
                name="lapsed",
                expect=[
                    Change(
                        resource="member",
                        op="changed",
                        id="MEM-0001",
                        fields={"status": "lapsed"},
                    )
                ],
            ),
        ],
    )

    result = score(story, seeded, twin.state(), Trajectory())

    assert result.passed
    assert result.outcome == "lapsed"


def test_an_empty_alternative_outcome_is_caught_by_the_null_agent(twin: Twin) -> None:
    story = Story(
        id="weak-outcome",
        intent="resolve",
        persona="cooperative",
        goal="resolve something",
        outcomes=[
            Outcome(name="changed", expect=[Change(resource="member", op="changed")]),
            Outcome(name="did nothing", expect=[]),
        ],
    )

    result = run_story(twin, story, NullAgent())

    assert result.trivially_passed


def test_temporal_never_catches_damage_repaired_before_the_end() -> None:
    pack = Pack.model_validate(
        {
            "name": "notes",
            "resources": {"note": {"id_field": "id", "id_prefix": "N-"}},
            "routes": {
                "add_note": {"resource": "note", "verb": "create"},
                "drop_note": {"resource": "note", "verb": "delete"},
            },
        }
    )
    story = Story(
        id="transient",
        intent="edit",
        persona="cooperative",
        goal="leave notes alone",
        never=[Change(resource="note", op="deleted")],
        ever=[Change(resource="note", op="created")],
    )
    agent = ScriptedAgent(
        "damages", {"transient": [Call("add_note", {}), Call("drop_note", {"id": "N-0001"})]}
    )

    trial = play(Twin(pack), story, agent)

    assert trial.score.state_ok, "the endpoint is identical"
    assert not trial.score.temporal_ok
    assert any("forbidden transient change" in reason for reason in trial.score.reasons)
    assert not any("required transient change" in reason for reason in trial.score.reasons)


def test_temporal_ever_fails_when_the_required_event_never_happens(twin: Twin) -> None:
    story = Story(
        id="ever",
        intent="file",
        persona="cooperative",
        goal="file something",
        ever=[Change(resource="claim", op="created")],
    )

    trial = play(twin, story, NullAgent())

    assert not trial.score.temporal_ok
    assert "required transient change never happened" in trial.score.reasons[0]


def test_story_contract_rejects_ambiguous_configuration() -> None:
    with pytest.raises(ValueError, match="outcomes or expect"):
        Story(
            id="ambiguous",
            intent="x",
            persona="x",
            goal="x",
            expect=[Change(resource="claim")],
            outcomes=[Outcome(name="also", expect=[])],
        )
    with pytest.raises(ValueError, match="exactly one"):
        CallExpectation(operation="get_member", any_of=["list_claims"])
    with pytest.raises(ValueError, match="labels conflict"):
        Story(
            id="labels",
            intent="lookup",
            persona="cooperative",
            goal="x",
            labels={"intent": "pay"},
        )


def test_duplicate_story_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "stories.yaml"
    path.write_text(
        "- {id: a, intent: i, persona: p, goal: g}\n- {id: a, intent: i, persona: p, goal: g}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate story ids"):
        load_stories(path)
