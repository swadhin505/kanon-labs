"""The verdict: an exit code and a comment you can paste into a pull request.

Three ways to fail, in order of how much they should worry you:

1. **A slice regressed.** Something that used to work reliably now doesn't.
2. **A story is trivially passable.** An agent that does nothing passes it, so it
   is inflating the score rather than measuring anything.
3. **The twin was asked for something it does not cover.** The run happened in a
   world with a hole in it, so the numbers describe that world, not yours.

Only the first is a regression. The other two are the honesty rules -- a gate
that reports a clean 100% while quietly resting on either of them is the exact
failure mode this product exists to refuse.
"""

from __future__ import annotations

from dataclasses import dataclass

from kanon.gate.metrics import RunReport
from kanon.gate.regression import SliceDelta, compare


@dataclass(frozen=True)
class Verdict:
    ok: bool
    reasons: list[str]

    @property
    def exit_code(self) -> int:
        return 0 if self.ok else 1


def evaluate(
    current: RunReport,
    baseline: RunReport | None,
    k: int,
    max_drop: float = 0.0,
    policy_max_drop: float = 0.0,
) -> tuple[Verdict, list[SliceDelta]]:
    """Decide pass/fail, with a separate tolerance for policy slices."""
    deltas = compare(baseline, current, k)
    reasons = []

    for delta in deltas:
        threshold = policy_max_drop if delta.slice[1] != "-" else max_drop
        if delta.status == "regressed" and -delta.delta > threshold:
            reasons.append(f"slice regressed: {delta.describe()}")
        if delta.status == "gone":
            reasons.append(f"slice no longer covered: {delta.label}")

    for story in current.stories:
        if story.trivially_passed:
            reasons.append(
                f"{story.id} is passed by an agent that does nothing -- it measures nothing"
            )

    for operation, count in sorted(current.uncovered.items()):
        reasons.append(f"twin does not cover {operation} (asked for {count}x)")

    return Verdict(not reasons, reasons), deltas


def markdown(
    current: RunReport,
    baseline: RunReport | None,
    k: int,
    max_drop: float = 0.0,
    policy_max_drop: float = 0.0,
) -> str:
    """The PR comment."""
    verdict, deltas = evaluate(current, baseline, k, max_drop, policy_max_drop)
    aggregate = current.aggregate(k)

    lines = [
        f"### {'PASS' if verdict.ok else 'FAIL'} — {current.domain} / {current.agent}",
        "",
        f"pass@1 **{aggregate.pass_at_1:.2f}** · pass^{k} **{aggregate.pass_hat_k:.2f}** "
        f"· {len(current.stories)} stories × {current.trials} trials",
    ]
    if baseline is not None:
        before = baseline.aggregate(k)
        lines.append(
            f"baseline `{baseline.agent}`: pass^{k} {before.pass_hat_k:.2f} "
            f"({aggregate.pass_hat_k - before.pass_hat_k:+.2f} overall) — "
            "**the aggregate is not where the story is; read the table.**"
        )

    lines += ["", "| slice | pass^k | change | |", "|---|---|---|---|"]
    marks = {"regressed": "🔻", "improved": "🔺", "flat": "", "new": "new", "gone": "gone"}
    for delta in deltas:
        after = "—" if delta.after is None else f"{delta.after:.2f}"
        change = "—" if delta.status in ("new", "gone") else f"{delta.delta:+.2f}"
        lines.append(f"| {delta.label} | {after} | {change} | {marks[delta.status]} |")

    failures = [story for story in current.stories if not story.passed]
    if failures:
        lines += ["", "#### Failing stories"]
        for story in failures:
            lines.append(
                f"- **{story.id}** ({' / '.join(story.slice)}) "
                f"{story.successes}/{story.trials} trials"
            )
            lines += [f"  - {reason}" for reason in story.reasons]

    lines += ["", "#### Coverage and honesty"]
    no_rules = [s.id for s in current.stories if s.invariants == 0]
    trivial = [s.id for s in current.stories if s.trivially_passed]
    lines.append(
        f"- {len(current.stories) - len(no_rules)}/{len(current.stories)} stories check at "
        f"least one policy rule" + (f" (no rules: {', '.join(no_rules)})" if no_rules else "")
    )
    lines.append(f"- trivially passable stories: {', '.join(trivial) if trivial else 'none'}")
    if current.model_calls:
        lines.append(f"- model calls spent on this run: {current.model_calls}")
    lines.append(
        "- uncovered twin operations: "
        + (
            ", ".join(f"{op} ({n}x)" for op, n in sorted(current.uncovered.items()))
            if current.uncovered
            else "none"
        )
    )

    if not verdict.ok:
        lines += ["", "#### Why this failed"] + [f"- {reason}" for reason in verdict.reasons]

    return "\n".join(lines)
