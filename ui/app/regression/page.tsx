import Link from "next/link";
import { CopyMarkdown } from "@/components/copy-markdown";
import { Heatmap } from "@/components/heatmap";
import { gatePassed, k, markdownSummary, percent, regressed, slices } from "@/lib/data";

export default function RegressionPage() {
  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Current vs last green</p>
          <h1>Regression</h1>
          <p>The aggregate fell because one policy slice collapsed. The other four stayed flat.</p>
        </div>
        <CopyMarkdown value={markdownSummary()} />
      </div>

      <section className={`verdict ${gatePassed ? "verdict-pass" : "verdict-fail"}`}>
        <div>
          <span className="verdict-label">CI gate</span>
          <strong>{gatePassed ? "PASS" : "FAIL"}</strong>
        </div>
        <p>
          {regressed.length} slice regressed beyond tolerance. Safety-policy slices allow zero drop.
        </p>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <h2>Change in pass^{k}</h2>
            <p>Red means less reliable than the baseline; neutral cells did not move.</p>
          </div>
        </div>
        <Heatmap slices={slices} mode="delta" />
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <h2>Worst changes first</h2>
            <p>Sorted by delta so a collapsed slice cannot hide inside the aggregate.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Scenario</th><th>Slice</th><th>Baseline</th><th>Current</th><th>Δ pass^{k}</th><th>Status</th></tr>
            </thead>
            <tbody>
              {slices.map((slice) => (
                <tr key={slice.id}>
                  <td><Link href={`/scenarios/${slice.id}`}>{slice.id}</Link></td>
                  <td>{slice.label}</td>
                  <td>{percent(slice.before)}</td>
                  <td>{percent(slice.after)}</td>
                  <td className={slice.delta < 0 ? "negative" : "muted"}>{slice.delta >= 0 ? "+" : ""}{Math.round(slice.delta * 100)}pp</td>
                  <td><span className={`badge badge-${slice.status}`}>{slice.status}</span></td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
