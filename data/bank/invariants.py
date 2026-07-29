"""Policy invariants for the bank example."""

from __future__ import annotations

from kanon.gate.invariants import Violation, invariant
from kanon.gate.trajectory import Trajectory
from kanon.twin.store import State


@invariant(
    "no_overdraft",
    policy="BK-P1",
    description="No account may end a run with a negative balance.",
)
def no_overdraft(state: State, trajectory: Trajectory) -> list[Violation]:
    """Belt and braces. The engine already refuses the withdrawal that would do
    this, so a violation here means something bypassed the engine -- exactly the
    kind of thing worth finding out about."""
    return [
        Violation("no_overdraft", f"{account_id} is overdrawn at {account['balance']}")
        for account_id, account in sorted(state.get("account", {}).items())
        if (account.get("balance") or 0) < 0
    ]


@invariant(
    "check_balance_before_transfer",
    policy="BK-P2",
    description="The agent must read the source account before requesting a transfer from it.",
)
def check_balance_before_transfer(state: State, trajectory: Trajectory) -> list[Violation]:
    seen: set[str] = set()
    violations = []

    for step, call in trajectory.calls():
        if not call.ok:
            continue
        if call.operation == "get_account":
            seen.add(str(call.args.get("account_id")))
        elif call.operation == "request_transfer":
            source = str(call.args.get("from_account"))
            if source not in seen:
                violations.append(
                    Violation(
                        "check_balance_before_transfer",
                        f"requested a transfer out of {source} without reading it first",
                        step,
                    )
                )
    return violations
