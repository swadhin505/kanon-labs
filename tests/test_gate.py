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
from kanon.gate.story import Change, Story, UserTurn, load_stories
from kanon.gate.trajectory import Trajectory
from kanon.twin import Twin

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
    assert "never called get_member" in reasons
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
    """hi-003 expects no state change. Strip its `must_call` and an agent that
    does nothing passes it -- so the story proves nothing. tau2 ships real tasks
    with exactly this hole; we refuse to count them."""
    hollow = STORIES["hi-003"].model_copy(update={"must_call": []})

    assert play(twin, hollow, NullAgent()).score.passed
    assert run_story(twin, hollow, GOOD).trivially_passed
    assert not run_story(twin, STORIES["hi-003"], GOOD).trivially_passed


# --- story loading -------------------------------------------------------


def test_stories_carry_their_slice() -> None:
    assert STORIES["hi-001"].slice == ("file_claim", "HI-P1", "cooperative")
    assert STORIES["hi-004"].slice == ("check_status", "-", "cooperative")
    assert STORIES["hi-005"].user_turns[0].confirms == ["pay_claim:CLM-0002"]
    assert STORIES["hi-005"].user_turns[0].after_call == "approve_claim"


def test_confirmation_metadata_cannot_be_positional() -> None:
    with pytest.raises(ValueError, match="requires after_call"):
        UserTurn(content="yes", confirms=["pay_claim:CLM-0002"])


def test_duplicate_story_ids_are_rejected(tmp_path: Path) -> None:
    path = tmp_path / "stories.yaml"
    path.write_text(
        "- {id: a, intent: i, persona: p, goal: g}\n- {id: a, intent: i, persona: p, goal: g}\n",
        encoding="utf-8",
    )
    with pytest.raises(ValueError, match="duplicate story ids"):
        load_stories(path)
