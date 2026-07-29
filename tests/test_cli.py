"""The CLI's runnable check, which is also the end-to-end demo:
build a twin, record a green baseline, break the agent, watch CI go red.
"""

from __future__ import annotations

from pathlib import Path

import pytest

from kanon.cli import main

ROOT = Path(__file__).resolve().parents[1]
INSURANCE = str(ROOT / "data" / "health-insurance")


def test_build_describes_a_valid_domain(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["twin", "build", INSURANCE]) == 0

    out = capsys.readouterr().out
    assert "health-insurance" in out
    assert "claim      1 seeded" in out
    assert "11 operations" in out


def test_build_rejects_a_broken_pack(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    (tmp_path / "pack.yaml").write_text(
        "name: broken\n"
        "resources:\n"
        "  claim: {id_field: claim_id, state_field: status,"
        " transitions: {submitted: [aproved], approved: []}}\n"
        "routes: {}\n",
        encoding="utf-8",
    )

    assert main(["twin", "build", str(tmp_path)]) == 1
    assert "not declared states" in capsys.readouterr().err


def test_the_gate_is_green_then_red(tmp_path: Path, capsys: pytest.CaptureFixture[str]) -> None:
    """The demo, start to finish."""
    baseline = tmp_path / "green.json"

    # 1. The good agent passes everything, and we keep the result as the baseline.
    assert (
        main(
            ["gate", "run", INSURANCE, "--agent", "good", "--trials", "3", "--save", str(baseline)]
        )
        == 0
    )
    green = capsys.readouterr().out
    assert green.startswith("### PASS")
    assert "pass^3 **1.00**" in green
    assert baseline.exists()

    # 2. A prompt change lands. Same suite, same twin, one worse agent.
    assert (
        main(
            [
                "gate",
                "run",
                INSURANCE,
                "--agent",
                "broken",
                "--trials",
                "3",
                "--baseline",
                str(baseline),
            ]
        )
        == 1
    )
    red = capsys.readouterr().out

    assert red.startswith("### FAIL")
    assert "| file_claim / HI-P1 / cooperative | 0.00 | -1.00 | 🔻 |" in red
    assert "| file_claim / HI-P3 / adversarial | 0.00 | -1.00 | 🔻 |" in red
    assert "| check_status / - / cooperative | 1.00 | +0.00 |" in red, "untouched slice holds"
    assert "annual cap of 25000" in red, "the report says what actually broke"


def test_a_first_run_with_no_baseline_cannot_regress(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gate", "run", INSURANCE, "--agent", "broken"]) == 0
    out = capsys.readouterr().out
    assert out.startswith("### FAIL") is False
    assert "new" in out, "every slice is new on a first run"


def test_an_unknown_agent_is_an_error(capsys: pytest.CaptureFixture[str]) -> None:
    assert main(["gate", "run", INSURANCE, "--agent", "nope"]) == 1
    assert "available: broken, good, llm, llm-no-cap, subtle" in capsys.readouterr().err


def test_one_collapsed_slice_is_caught_even_though_three_still_pass(
    tmp_path: Path, capsys: pytest.CaptureFixture[str]
) -> None:
    """The regression the product exists for: only the adversarial slice breaks."""
    baseline = tmp_path / "green.json"
    main(["gate", "run", INSURANCE, "--agent", "good", "--trials", "3", "--save", str(baseline)])
    capsys.readouterr()

    assert (
        main(
            [
                "gate",
                "run",
                INSURANCE,
                "--agent",
                "subtle",
                "--trials",
                "3",
                "--baseline",
                str(baseline),
            ]
        )
        == 1
    )
    out = capsys.readouterr().out

    assert "| file_claim / HI-P3 / adversarial | 0.00 | -1.00 | 🔻 |" in out
    assert out.count("🔻") == 1, "exactly one slice moved"
    assert "annual cap of 25000" in out
