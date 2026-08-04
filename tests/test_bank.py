"""Generality check: a second domain, run through the same product code.

Nothing in `kanon/` knows what an account is. If banking works with no changes
beyond a pack, a rules file and some stories, the "works for your APIs" claim is
real. This file exists to keep it real -- it is the test that fails first if
insurance ever leaks into the engine.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.domain import Domain
from kanon.gate.runner import run_all
from kanon.twin import Twin, TwinError

BANK = Domain.load(Path(__file__).resolve().parents[1] / "data" / "bank")


@pytest.fixture
def twin() -> Twin:
    return Twin(BANK.pack)


def test_one_call_moves_money_between_two_records(twin: Twin) -> None:
    """The thing the insurance domain could never have exercised."""
    twin.call(
        "request_transfer", {"from_account": "ACC-0001", "to_account": "ACC-0002", "amount": 200}
    )
    twin.call("post_transfer", {"transfer_id": "TRF-0001"})

    assert twin.call("get_account", {"account_id": "ACC-0001"})["balance"] == 1000
    assert twin.call("get_account", {"account_id": "ACC-0002"})["balance"] == 250


def test_the_bank_refuses_to_overdraw(twin: Twin) -> None:
    twin.call(
        "request_transfer", {"from_account": "ACC-0002", "to_account": "ACC-0001", "amount": 5000}
    )

    with pytest.raises(TwinError) as refusal:
        twin.call("post_transfer", {"transfer_id": "TRF-0001"})

    assert refusal.value.code == "insufficient_funds"
    assert "below the minimum of 0" in refusal.value.message


def test_a_refused_transfer_moves_nothing_at_all(twin: Twin) -> None:
    """Atomicity. The first effect (debit) succeeds and the second is never
    reached -- but a twin that half-applies a transfer is worse than no twin."""
    before = twin.state()
    twin.call(
        "request_transfer", {"from_account": "ACC-0002", "to_account": "ACC-0001", "amount": 5000}
    )
    after_request = twin.state()

    with pytest.raises(TwinError):
        twin.call("post_transfer", {"transfer_id": "TRF-0001"})

    assert twin.state() == after_request, "the failed posting left no trace"
    assert twin.state()["account"] == before["account"], "no balance moved"
    assert twin.call("get_transfer", {"transfer_id": "TRF-0001"})["status"] == "requested"


def test_a_transfer_to_a_missing_account_is_refused_up_front(twin: Twin) -> None:
    """Referential integrity catches this at request time, before a transfer
    record exists at all -- earlier and cheaper than the effect-level 404, which
    stays as defence in depth for state the engine did not write."""
    with pytest.raises(TwinError) as refusal:
        twin.call(
            "request_transfer", {"from_account": "ACC-0001", "to_account": "ACC-9999", "amount": 10}
        )

    assert refusal.value.code == "unknown_reference"
    assert twin.state()["transfer"] == {}, "no dangling transfer was created"
    assert twin.call("get_account", {"account_id": "ACC-0001"})["balance"] == 1200


def test_the_whole_gate_runs_on_a_domain_it_has_never_seen(twin: Twin) -> None:
    results = run_all(twin, BANK.stories, BANK.agent("teller"), trials=2)

    failures = {r.story.id: r.trials[0].score.reasons for r in results if r.successes < 2}
    assert failures == {}
    assert not any(r.trivially_passed for r in results)
