"""pass^k, the per-slice comparison, and the gate's honesty rules."""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.domain import Domain
from kanon.gate.ci import evaluate, markdown
from kanon.gate.metrics import RunReport, StorySummary, pass_hat_k
from kanon.gate.regression import compare
from kanon.gate.runner import run_all
from kanon.twin import Twin

INSURANCE = Path(__file__).resolve().parents[1] / "data" / "health-insurance"


def summary(story_id: str, slice_: tuple[str, str, str], trials: int, successes: int, **kw):
    intent, policy, persona = slice_
    return StorySummary(
        id=story_id,
        intent=intent,
        policy=policy,
        persona=persona,
        trials=trials,
        successes=successes,
        trivially_passed=kw.get("trivially_passed", False),
        invariants=kw.get("invariants", 1),
    )


# --- the formula ---------------------------------------------------------


def test_pass_hat_k_is_worst_case_not_best_case() -> None:
    assert pass_hat_k(5, 5, 1) == 1.0
    assert pass_hat_k(5, 5, 5) == 1.0
    # 4 of 5 looks fine at pass@1 and is exposed the moment you ask for 5 in a row.
    assert pass_hat_k(5, 4, 1) == 0.8
    assert pass_hat_k(5, 4, 5) == 0.0
    assert pass_hat_k(5, 4, 2) == 0.6
    assert pass_hat_k(3, 0, 1) == 0.0


def test_pass_hat_k_refuses_a_k_it_cannot_answer() -> None:
    with pytest.raises(ValueError, match="k must be between"):
        pass_hat_k(3, 3, 4)


# --- the thing the product exists for ------------------------------------


def test_a_collapsed_slice_is_invisible_in_the_aggregate() -> None:
    """Four slices. One dies completely, one improves. The headline barely
    moves; the per-slice table shows the corpse."""
    before = RunReport(
        "d",
        "v1",
        [
            summary("a", ("refund", "P1", "cooperative"), 4, 4),
            summary("b", ("refund", "P2", "adversarial"), 4, 2),
            summary("c", ("status", "P1", "cooperative"), 4, 3),
            summary("d", ("status", "P3", "confused"), 4, 2),
        ],
    )
    after = RunReport(
        "d",
        "v2",
        [
            summary("a", ("refund", "P1", "cooperative"), 4, 0),  # collapsed
            summary("b", ("refund", "P2", "adversarial"), 4, 4),  # improved
            summary("c", ("status", "P1", "cooperative"), 4, 4),  # improved
            summary("d", ("status", "P3", "confused"), 4, 3),  # improved
        ],
    )

    assert before.aggregate(1).pass_at_1 == 0.6875
    assert after.aggregate(1).pass_at_1 == 0.6875, "the headline is identical"

    deltas = {d.label: d for d in compare(before, after, k=1)}
    assert deltas["refund / P1 / cooperative"].status == "regressed"
    assert deltas["refund / P1 / cooperative"].delta == -1.0

    verdict, ordered = evaluate(after, before, k=1)
    assert not verdict.ok
    assert ordered[0].label == "refund / P1 / cooperative", "worst slice sorts first"
    assert "slice regressed" in verdict.reasons[0]


def test_a_tolerated_wobble_does_not_fail_the_build() -> None:
    before = RunReport("d", "v1", [summary("a", ("i", "-", "p"), 4, 4)])
    after = RunReport("d", "v2", [summary("a", ("i", "-", "p"), 4, 3)])

    assert not evaluate(after, before, k=1)[0].ok
    assert evaluate(after, before, k=1, max_drop=0.25)[0].ok


def test_policy_slices_keep_zero_tolerance_when_ordinary_slices_have_slack() -> None:
    before = RunReport("d", "v1", [summary("a", ("i", "P1", "p"), 4, 4)])
    after = RunReport("d", "v2", [summary("a", ("i", "P1", "p"), 4, 3)])

    assert not evaluate(after, before, k=1, max_drop=0.25)[0].ok
    assert evaluate(after, before, k=1, max_drop=0.25, policy_max_drop=0.25)[0].ok


def test_improvements_never_fail_the_build() -> None:
    before = RunReport("d", "v1", [summary("a", ("i", "P1", "p"), 2, 1)])
    after = RunReport("d", "v2", [summary("a", ("i", "P1", "p"), 2, 2)])
    assert evaluate(after, before, k=1)[0].ok


# --- the honesty rules ---------------------------------------------------


def test_a_story_that_cannot_fail_turns_the_build_red() -> None:
    report = RunReport("d", "v1", [summary("a", ("i", "P1", "p"), 2, 2, trivially_passed=True)])
    verdict, _ = evaluate(report, None, k=1)
    assert not verdict.ok
    assert "measures nothing" in verdict.reasons[0]


def test_an_uncovered_twin_operation_turns_the_build_red() -> None:
    report = RunReport("d", "v1", [summary("a", ("i", "P1", "p"), 2, 2)], {"appeal_claim": 3})
    verdict, _ = evaluate(report, None, k=1)
    assert not verdict.ok
    assert "does not cover appeal_claim (asked for 3x)" in verdict.reasons[0]


def test_a_slice_that_vanished_turns_the_build_red() -> None:
    before = RunReport("d", "v1", [summary("a", ("i", "P1", "p"), 2, 2)])
    after = RunReport("d", "v2", [])
    verdict, _ = evaluate(after, before, k=1)
    assert not verdict.ok
    assert "no longer covered" in verdict.reasons[0]


# --- round trip and rendering, on the real domain ------------------------


def test_a_report_survives_a_round_trip_to_disk(tmp_path: Path) -> None:
    domain = Domain.load(INSURANCE)
    twin = Twin(domain.pack)
    results = run_all(twin, domain.stories, domain.agent("good"), trials=2)
    report = RunReport.from_results(domain.name, "good", results, twin.uncovered)

    reloaded = RunReport.load(report.save(tmp_path / "run.json"))
    assert reloaded == report
    assert reloaded.aggregate(2).pass_hat_k == 1.0


def test_the_report_names_the_stories_with_no_rules_attached() -> None:
    domain = Domain.load(INSURANCE)
    twin = Twin(domain.pack)
    results = run_all(twin, domain.stories, domain.agent("good"))
    report = RunReport.from_results(domain.name, "good", results, twin.uncovered)

    rendered = markdown(report, None, k=1)
    assert "4/5 stories check at least one policy rule (no rules: hi-004)" in rendered
    assert "trivially passable stories: none" in rendered
    assert "uncovered twin operations: none" in rendered
