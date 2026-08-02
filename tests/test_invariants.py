"""The invariants' runnable check: a clean run passes, a violating run fails and
says which step did it.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.gate import Trajectory, invariants
from kanon.gate.trajectory import Message, ToolCall
from kanon.twin import Pack, Twin, TwinError

DOMAIN = Path(__file__).resolve().parents[1] / "data" / "health-insurance"

invariants.load(DOMAIN / "invariants.py")


@pytest.fixture
def twin() -> Twin:
    return Twin(Pack.from_yaml(DOMAIN / "pack.yaml"))


def run(twin: Twin, script: list[tuple[str, dict]]) -> Trajectory:
    """Execute a scripted list of tool calls and record what happened.

    ponytail: the seed of the Phase 3 runner. It moves to gate/runner.py the
    moment a real agent, rather than a fixed script, decides the calls.
    """
    trajectory = Trajectory()
    for operation, args in script:
        try:
            result = twin.call(operation, args)
        except TwinError as exc:
            trajectory.add(ToolCall(operation, args, error=exc.code))
        else:
            trajectory.add(ToolCall(operation, args, result=result))
    return trajectory


def test_the_domain_registers_its_rules() -> None:
    loaded = invariants.load(DOMAIN / "invariants.py")  # second load is a no-op
    assert {i.name for i in loaded} == {
        "payout_within_annual_cap",
        "verify_member_before_claim",
        "confirm_before_paying",
    }
    assert invariants.registered()["payout_within_annual_cap"].policy == "HI-P3"


def test_a_compliant_run_passes(twin: Twin) -> None:
    trajectory = run(
        twin,
        [
            ("get_member", {"member_id": "MEM-0001"}),
            ("submit_claim", {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800}),
            ("review_claim", {"claim_id": "CLM-0002"}),
            ("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 720}),
        ],
    )
    assert invariants.check(twin.state(), trajectory) == []


def test_payout_over_the_cap_is_caught_and_the_step_is_cited(twin: Twin) -> None:
    trajectory = run(
        twin,
        [
            ("get_member", {"member_id": "MEM-0001"}),
            ("submit_claim", {"member_id": "MEM-0001", "service_code": "D6010", "amount": 40_000}),
            ("review_claim", {"claim_id": "CLM-0002"}),
            ("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 40_000}),
        ],
    )

    found = invariants.check(twin.state(), trajectory, ["payout_within_annual_cap"])
    assert len(found) == 1
    assert "annual cap of 25000" in found[0].message
    assert found[0].step == 3, "the approve_claim call is what broke it"
    assert "approve_claim" in found[0].render(trajectory)


def test_filing_without_checking_coverage_is_caught(twin: Twin) -> None:
    trajectory = run(
        twin,
        [("submit_claim", {"member_id": "MEM-0002", "service_code": "D0120", "amount": 100})],
    )

    found = invariants.check(twin.state(), trajectory, ["verify_member_before_claim"])
    assert [(v.rule, v.step) for v in found] == [("verify_member_before_claim", 0)]


def test_a_refused_call_does_not_count_as_verification(twin: Twin) -> None:
    trajectory = run(
        twin,
        [
            ("get_member", {"member_id": "MEM-9999"}),  # 404
            ("submit_claim", {"member_id": "MEM-9999", "service_code": "D0120", "amount": 100}),
        ],
    )

    found = invariants.check(twin.state(), trajectory, ["verify_member_before_claim"])
    assert len(found) == 1, "a lookup that failed proves nothing"


def test_an_unattributable_payout_is_reported_not_ignored(twin: Twin) -> None:
    """The agent invents a member id. There is no cap to compare against --
    which is a finding, not a pass."""
    trajectory = run(
        twin,
        [
            ("submit_claim", {"member_id": "GHOST-1", "service_code": "D0120", "amount": 50}),
            ("review_claim", {"claim_id": "CLM-0002"}),
            ("approve_claim", {"claim_id": "CLM-0002", "approved_amount": 50}),
        ],
    )

    found = invariants.check(twin.state(), trajectory, ["payout_within_annual_cap"])
    assert "cap cannot be checked" in found[0].message


def test_an_unknown_invariant_raises(twin: Twin) -> None:
    with pytest.raises(KeyError, match="no_such_rule"):
        invariants.check(twin.state(), Trajectory(), ["no_such_rule"])


def test_payment_confirmation_is_ordered_and_exact() -> None:
    early = Trajectory(
        [
            ToolCall("pay_claim", {"claim_id": "CLM-0002"}),
            Message("user", "Yes, pay it.", ("pay_claim:CLM-0002",)),
        ]
    )
    assert len(invariants.check({}, early, ["confirm_before_paying"])) == 1

    confirmed = Trajectory(
        [
            Message("user", "Yes, pay it.", ("pay_claim:CLM-0002",)),
            ToolCall("pay_claim", {"claim_id": "CLM-0002"}),
        ]
    )
    assert invariants.check({}, confirmed, ["confirm_before_paying"]) == []
