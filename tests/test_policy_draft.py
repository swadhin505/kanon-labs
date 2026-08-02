from __future__ import annotations

import ast
from pathlib import Path

from kanon.cli import main


def test_policy_drafts_are_valid_python_and_cannot_silently_pass(tmp_path: Path) -> None:
    source = tmp_path / "policies.yaml"
    output = tmp_path / "invariants.draft.py"
    source.write_text(
        "- id: PAY-1\n"
        "  name: confirm_payment\n"
        "  violation: Payment happens without confirmation.\n",
        encoding="utf-8",
    )

    assert main(["policy", "draft", str(source), "-o", str(output)]) == 0
    rendered = output.read_text(encoding="utf-8")
    ast.parse(rendered)
    assert "@invariant('confirm_payment', policy='PAY-1'" in rendered
    assert "raise NotImplementedError" in rendered

    assert main(["policy", "draft", str(source), "-o", str(output)]) == 1
