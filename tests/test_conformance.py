from pathlib import Path

import pytest

pytest.importorskip("schemathesis")

from kanon.conformance import fuzz
from kanon.domain import Domain

DOMAIN = Path(__file__).resolve().parents[1] / "data" / "health-insurance"


def test_every_served_operation_survives_generated_valid_and_invalid_inputs() -> None:
    report = fuzz(Domain.load(DOMAIN).pack, examples=3, seed=7)

    assert report.operations == 11
    assert report.cases >= report.operations
