"""This run against the last green one, slice by slice.

The whole product is this comparison. An aggregate score is nearly useless for
catching drift: change a prompt, watch the headline stay at 67%, and never
notice that one slice went from 100% to 33% while two others crept up. Teams
find that out weeks later from a support ticket.

Comparing per slice makes the collapse impossible to average away.
"""

from __future__ import annotations

from dataclasses import dataclass
from typing import Literal

from kanon.gate.metrics import RunReport, Slice, SliceMetrics, slice_label

Status = Literal["regressed", "improved", "flat", "new", "gone"]


@dataclass(frozen=True)
class SliceDelta:
    slice: Slice
    before: float | None
    after: float | None
    status: Status

    @property
    def delta(self) -> float:
        if self.before is None or self.after is None:
            return 0.0
        return self.after - self.before

    @property
    def label(self) -> str:
        return slice_label(self.slice)

    def describe(self) -> str:
        if self.status == "new":
            return f"{self.label}: new slice, pass^k {self.after:.2f}"
        if self.status == "gone":
            return f"{self.label}: slice disappeared (was {self.before:.2f})"
        return f"{self.label}: pass^k {self.before:.2f} -> {self.after:.2f} ({self.delta:+.2f})"


def compare(baseline: RunReport | None, current: RunReport, k: int) -> list[SliceDelta]:
    """Per-slice change in pass^k, worst first.

    With no baseline every slice is `new`: a first run cannot regress, but it
    still reports its absolute numbers.
    """
    now: dict[Slice, SliceMetrics] = {m.slice: m for m in current.slices(k)}
    then: dict[Slice, SliceMetrics] = (
        {m.slice: m for m in baseline.slices(k)} if baseline is not None else {}
    )

    deltas = []
    for key in sorted(set(now) | set(then)):
        before = then[key].pass_hat_k if key in then else None
        after = now[key].pass_hat_k if key in now else None

        if before is None:
            status: Status = "new"
        elif after is None:
            status = "gone"
        elif after < before:
            status = "regressed"
        elif after > before:
            status = "improved"
        else:
            status = "flat"
        deltas.append(SliceDelta(key, before, after, status))

    # Worst first. That ordering is the whole point of the table.
    return sorted(deltas, key=lambda d: (d.delta, d.label))
