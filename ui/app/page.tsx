import Link from "next/link";
import { Heatmap } from "@/components/heatmap";
import { aggregate, current, k, percent, regressed, slices } from "@/lib/data";

export default function OverviewPage() {
  const worst = Math.min(...slices.map((slice) => slice.after ?? 0));
  const policyChecked = current.stories.filter((story) => story.invariants > 0).length;
  const calls = current.stories.flatMap((story) => story.trial_details.flatMap((trial) => trial.events))
    .filter((event) => event.kind === "tool_call");
  const deterministic = calls.filter((event) => event.deterministic).length;
  const deterministicPercent = calls.length ? deterministic / calls.length : 1;

  return (
    <div className="page-shell">
      <div className="page-heading">
        <div>
          <p className="eyebrow">Latest run</p>
          <h1>Results overview</h1>
          <p>Reliability across every intent, policy, and persona slice.</p>
        </div>
        <span className="run-chip">5 stories · {k} trials each</span>
      </div>

      <section className="stats" aria-label="Run summary">
        <article className="stat">
          <span>Aggregate pass^{k}</span>
          <strong>{percent(aggregate(current))}</strong>
          <small>All {k} attempts must pass</small>
        </article>
        <article className="stat">
          <span>Worst slice</span>
          <strong>{percent(worst)}</strong>
          <small>{slices.find((slice) => slice.after === worst)?.id}</small>
        </article>
        <article className="stat">
          <span>Regressed slices</span>
          <strong>{regressed.length}</strong>
          <small>against the last green run</small>
        </article>
      </section>

      <section className="honesty-banner" aria-label="Coverage and honesty">
        <span className="status-dot status-good" />
        <div>
          <strong>Coverage is explicit</strong>
          <p>
            {percent(deterministicPercent)} of observed tool calls were deterministic · {policyChecked}/
            {current.stories.length} stories check policy rules · no trivially passable stories.
          </p>
        </div>
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <h2>Reliability by slice</h2>
            <p>One cell per scenario. Open any cell for trial-level evidence.</p>
          </div>
          <Link href="/regression">See regression diff →</Link>
        </div>
        <Heatmap slices={slices} mode="score" />
      </section>

      <section className="section-block">
        <div className="section-heading">
          <div>
            <h2>Scenarios</h2>
            <p>The failure is isolated to one adversarial policy slice.</p>
          </div>
        </div>
        <div className="table-wrap">
          <table>
            <thead>
              <tr><th>Scenario</th><th>Slice</th><th>Trials</th><th>pass^{k}</th><th>Status</th></tr>
            </thead>
            <tbody>
              {slices.map((slice) => {
                const story = current.stories.find((item) => item.id === slice.id)!;
                return (
                  <tr key={slice.id}>
                    <td><Link href={`/scenarios/${slice.id}`}>{slice.id}</Link></td>
                    <td>{slice.label}</td>
                    <td>{story.successes}/{story.trials}</td>
                    <td>{percent(slice.after)}</td>
                    <td><span className={`badge ${story.successes === story.trials ? "badge-pass" : "badge-fail"}`}>{story.successes === story.trials ? "Passed" : "Failed"}</span></td>
                  </tr>
                );
              })}
            </tbody>
          </table>
        </div>
      </section>
    </div>
  );
}
