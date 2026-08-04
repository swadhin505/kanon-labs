import baselineData from "@/data/baseline.json";
import currentData from "@/data/current.json";
import type { RunReport, SliceResult, StorySummary } from "@/lib/types";

export const baseline = baselineData as RunReport;
export const current = currentData as RunReport;
export const k = Math.max(...current.stories.map((story) => story.trials), 1);

function choose(n: number, r: number) {
  if (r < 0 || r > n) return 0;
  let value = 1;
  for (let i = 1; i <= r; i += 1) value = (value * (n - r + i)) / i;
  return value;
}

export function passHatK(story: StorySummary) {
  if (story.successes < k) return 0;
  return choose(story.successes, k) / choose(story.trials, k);
}

export function passAtOne(story: StorySummary) {
  return story.successes / story.trials;
}

export function aggregate(report: RunReport) {
  const values = report.stories.map(passHatK);
  return values.reduce((sum, value) => sum + value, 0) / Math.max(values.length, 1);
}

const beforeById = new Map(baseline.stories.map((story) => [story.id, story]));
const afterById = new Map(current.stories.map((story) => [story.id, story]));

export const slices: SliceResult[] = [...new Set([...beforeById.keys(), ...afterById.keys()])]
  .map((id) => {
    const beforeStory = beforeById.get(id);
    const afterStory = afterById.get(id);
    const before = beforeStory ? passHatK(beforeStory) : null;
    const after = afterStory ? passHatK(afterStory) : null;
    const delta = before === null || after === null ? 0 : after - before;
    const source = afterStory ?? beforeStory!;
    let status: SliceResult["status"] = "flat";
    if (before === null) status = "new";
    else if (after === null) status = "gone";
    else if (delta < 0) status = "regressed";
    else if (delta > 0) status = "improved";
    return {
      id,
      label: `${source.intent} / ${source.policy} / ${source.persona}`,
      intent: source.intent,
      policy: source.policy,
      persona: source.persona,
      before,
      after,
      passAtOne: afterStory ? passAtOne(afterStory) : 0,
      delta,
      status,
    };
  })
  .sort((a, b) => a.delta - b.delta || a.label.localeCompare(b.label));

export const regressed = slices.filter((slice) => slice.status === "regressed");
export const gatePassed =
  regressed.length === 0 &&
  current.stories.every((story) => !story.trivially_passed) &&
  Object.keys(current.uncovered).length === 0;

export function percent(value: number | null) {
  return value === null ? "—" : `${Math.round(value * 100)}%`;
}

export function markdownSummary() {
  const lines = [
    `### ${gatePassed ? "PASS" : "FAIL"} — ${current.domain} / ${current.agent}`,
    "",
    `pass^${k} **${percent(aggregate(current))}** · ${current.stories.length} stories × ${k} trials`,
    "",
    "| slice | baseline | current | change |",
    "|---|---:|---:|---:|",
  ];
  for (const slice of slices) {
    lines.push(
      `| ${slice.label} | ${percent(slice.before)} | ${percent(slice.after)} | ${slice.delta >= 0 ? "+" : ""}${Math.round(slice.delta * 100)}pp |`,
    );
  }
  return lines.join("\n");
}
