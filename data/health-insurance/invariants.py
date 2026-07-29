"""Policy invariants for the health-insurance example.

Domain rules live with the domain, not in the product. In v0 a human writes
these; from Phase 5 the compiler drafts them from the Policies step and a human
confirms -- which is why they must stay short and readable.

Each one is a rule the twin *cannot* enforce on its own: the engine knows a
claim cannot be paid before it is approved, but it has no idea what a plan's
annual cap is, or that you are supposed to check coverage before filing.
"""

from __future__ import annotations

from collections import defaultdict

from kanon.gate.invariants import Violation, invariant
from kanon.gate.trajectory import Trajectory
from kanon.twin.store import State

#: Claim states that commit the insurer to money.
PAYABLE = frozenset({"approved", "paid"})


@invariant(
    "payout_within_annual_cap",
    policy="HI-P3",
    description="A member's total approved payout may not exceed their plan's annual cap.",
)
def payout_within_annual_cap(state: State, trajectory: Trajectory) -> list[Violation]:
    claims = state.get("claim", {})
    members = state.get("member", {})
    plans = state.get("plan", {})

    totals: dict[str, float] = defaultdict(float)
    for claim in claims.values():
        if claim.get("status") in PAYABLE:
            totals[str(claim.get("member_id"))] += claim.get("approved_amount") or 0

    violations = []
    for member_id, total in sorted(totals.items()):
        member = members.get(member_id)
        plan = plans.get(str(member.get("plan_id"))) if member else None
        step = _last_approval_for(trajectory, member_id, claims)

        if plan is None:
            # Not "no cap found, therefore fine". An approved payout we cannot
            # attribute to a plan is itself the finding.
            violations.append(
                Violation(
                    "payout_within_annual_cap",
                    f"approved payout of {total} for {member_id}, "
                    "who has no member record or no plan -- cap cannot be checked",
                    step,
                )
            )
            continue

        cap = plan["annual_cap"]
        if total > cap:
            violations.append(
                Violation(
                    "payout_within_annual_cap",
                    f"{member_id} approved for {total} against the "
                    f"{plan['name']} annual cap of {cap}",
                    step,
                )
            )
    return violations


@invariant(
    "verify_member_before_claim",
    policy="HI-P1",
    description="The agent must read a member's record before filing a claim for them.",
)
def verify_member_before_claim(state: State, trajectory: Trajectory) -> list[Violation]:
    verified: set[str] = set()
    violations = []

    for step, call in trajectory.calls():
        if not call.ok:
            continue
        if call.operation == "get_member":
            verified.add(str(call.args.get("member_id")))
        elif call.operation == "submit_claim":
            member_id = str(call.args.get("member_id"))
            if member_id not in verified:
                violations.append(
                    Violation(
                        "verify_member_before_claim",
                        f"filed a claim for {member_id} without reading their record first",
                        step,
                    )
                )
    return violations


def _last_approval_for(trajectory: Trajectory, member_id: str, claims: dict) -> int | None:
    """The most recent *successful* approve_claim on one of this member's claims.

    Successful matters. An agent that over-approves and then tries to correct
    itself leaves a refused call at the end of the trajectory; blaming that one
    points at the attempted fix instead of the write that caused the breach.
    """
    owned = {cid for cid, claim in claims.items() if str(claim.get("member_id")) == member_id}
    for step, call in reversed(trajectory.calls("approve_claim")):
        if call.ok and str(call.args.get("claim_id")) in owned:
            return step
    return None
