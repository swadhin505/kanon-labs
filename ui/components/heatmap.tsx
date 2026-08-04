import Link from "next/link";
import type { SliceResult } from "@/lib/types";
import { percent } from "@/lib/data";

type Props = {
  slices: SliceResult[];
  mode: "score" | "delta";
};

export function Heatmap({ slices, mode }: Props) {
  return (
    <div>
      <div className="heatmap" role="list" aria-label={`${mode} by scenario slice`}>
        {slices.map((slice) => {
          const value = mode === "score" ? slice.after ?? 0 : slice.delta;
          const className =
            mode === "delta"
              ? value < 0
                ? "cell cell-regressed"
                : value > 0
                  ? "cell cell-improved"
                  : "cell cell-flat"
              : value >= 1
                ? "cell cell-strong"
                : value > 0
                  ? "cell cell-partial"
                  : "cell cell-failed";
          const measure =
            mode === "score"
              ? `pass^k ${percent(slice.after)}, pass@1 ${percent(slice.passAtOne)}`
              : `change ${slice.delta >= 0 ? "+" : ""}${Math.round(slice.delta * 100)} percentage points`;
          return (
            <Link
              className={className}
              href={`/scenarios/${slice.id}`}
              key={slice.id}
              role="listitem"
              aria-label={`${slice.id}, ${slice.label}, ${measure}`}
            >
              {slice.id}
            </Link>
          );
        })}
      </div>
      <div className="legend" aria-label="Heatmap legend">
        {mode === "score" ? (
          <>
            <span><i className="swatch swatch-failed" />Failed</span>
            <span><i className="swatch swatch-partial" />Partial</span>
            <span><i className="swatch swatch-strong" />Reliable</span>
          </>
        ) : (
          <>
            <span><i className="swatch swatch-regressed" />Regressed</span>
            <span><i className="swatch swatch-flat" />Flat</span>
            <span><i className="swatch swatch-improved" />Improved</span>
          </>
        )}
      </div>
    </div>
  );
}
