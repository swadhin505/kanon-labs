import Link from "next/link";
import { notFound } from "next/navigation";
import { current, storyLabel } from "@/lib/data";

export function generateStaticParams() {
  return current.stories.map((story) => ({ id: story.id }));
}

export default async function ScenarioPage({
  params,
  searchParams,
}: {
  params: Promise<{ id: string }>;
  searchParams: Promise<{ trial?: string }>;
}) {
  const { id } = await params;
  const query = await searchParams;
  const story = current.stories.find((item) => item.id === id);
  if (!story) notFound();
  const requested = Number(query.trial ?? 1);
  const trialIndex = Number.isInteger(requested) && requested > 0 && requested <= story.trial_details.length ? requested - 1 : 0;
  const trial = story.trial_details[trialIndex];
  const violatedSteps = new Set(
    trial.invariants.flatMap((item) => item.violations.map((violation) => violation.step)).filter((step): step is number => step !== null),
  );

  return (
    <div className="page-shell detail-shell">
      <div className="breadcrumbs"><Link href="/regression">Regression</Link><span>/</span><span>{story.id}</span></div>
      <div className="page-heading">
        <div>
          <p className="eyebrow">{storyLabel(story).replaceAll(" / ", " · ")}</p>
          <h1>{story.id}</h1>
          <p>{story.successes}/{story.trials} trials passed. This view shows the evidence used by the deterministic scorer.</p>
        </div>
        <span className={`badge ${trial.passed ? "badge-pass" : "badge-fail"}`}>{trial.passed ? "Passed" : "Failed"}</span>
      </div>

      <nav className="trial-strip" aria-label="Trials">
        {story.trial_details.map((item, index) => (
          <Link className={index === trialIndex ? "trial active" : "trial"} href={`/scenarios/${story.id}?trial=${index + 1}`} key={index} aria-current={index === trialIndex ? "page" : undefined}>
            Trial {index + 1}<span>{item.passed ? "Pass" : "Fail"}</span>
          </Link>
        ))}
      </nav>

      <div className="detail-grid">
        <section className="section-block transcript-section">
          <div className="section-heading"><div><h2>Transcript and tool calls</h2><p>The violated step is marked in red.</p></div></div>
          <ol className="timeline">
            {trial.events.map((event) => (
              <li className={violatedSteps.has(event.step) ? "timeline-item violated" : "timeline-item"} id={`step-${event.step}`} key={event.step}>
                <span className="step">{event.step}</span>
                {event.kind === "message" ? (
                  <div className="message-event">
                    <span className={`role role-${event.role}`}>{event.role}</span>
                    <p>{event.content}</p>
                    {event.confirms.length > 0 && <small>Confirms: {event.confirms.join(", ")}</small>}
                  </div>
                ) : (
                  <details className="tool-event" open={violatedSteps.has(event.step)}>
                    <summary>
                      <code>{event.operation}</code>
                      <span className={`badge ${event.error ? "badge-fail" : "badge-pass"}`}>{event.error ?? "ok"}</span>
                      <span className="badge badge-neutral">{event.deterministic ? "deterministic" : "stubbed"}</span>
                    </summary>
                    <div className="tool-payloads">
                      <div><span>Arguments</span><pre>{JSON.stringify(event.args, null, 2)}</pre></div>
                      <div><span>Mocked response</span><pre>{JSON.stringify(event.result, null, 2)}</pre></div>
                    </div>
                  </details>
                )}
              </li>
            ))}
          </ol>
        </section>

        <aside>
          <section className="section-block compact-section">
            <div className="section-heading"><div><h2>Invariant checklist</h2><p>Pure Python rules; no LLM judge.</p></div></div>
            <ul className="checklist">
              {trial.invariants.map((item) => (
                <li key={item.name}>
                  <span className={item.passed ? "check check-pass" : "check check-fail"}>{item.passed ? "✓" : "×"}</span>
                  <div>
                    <strong>{item.name}</strong>
                    <p>{item.description}</p>
                    {item.violations.map((violation) => (
                      <p className="violation-copy" key={`${item.name}-${violation.step}`}>
                        {violation.message}{violation.step !== null && <> · <a href={`#step-${violation.step}`}>step {violation.step}</a></>}
                      </p>
                    ))}
                  </div>
                </li>
              ))}
            </ul>
          </section>

          <section className="section-block compact-section">
            <div className="section-heading"><div><h2>Score components</h2></div></div>
            <dl className="score-list">
              <div><dt>Expected state</dt><dd>{trial.state_ok ? "Pass" : "Fail"}</dd></div>
              <div><dt>Required calls</dt><dd>{trial.calls_ok ? "Pass" : "Fail"}</dd></div>
              <div><dt>Policy invariants</dt><dd className={trial.invariants_ok ? "" : "negative"}>{trial.invariants_ok ? "Pass" : "Fail"}</dd></div>
              <div><dt>User interaction</dt><dd className={trial.interaction_ok === false ? "negative" : ""}>{trial.interaction_ok === false ? "Fail" : "Pass"}</dd></div>
              <div><dt>Temporal checks</dt><dd className={trial.temporal_ok === false ? "negative" : ""}>{trial.temporal_ok === false ? "Fail" : "Pass"}</dd></div>
              {trial.outcome ? <div><dt>Matched outcome</dt><dd>{trial.outcome}</dd></div> : null}
            </dl>
          </section>

          <section className="section-block compact-section">
            <div className="section-heading"><div><h2>Trace</h2></div></div>
            <p className="muted">No Langfuse URL was recorded for this local run.</p>
          </section>
        </aside>
      </div>

      <section className="section-block">
        <div className="section-heading"><div><h2>Twin state diff</h2><p>Only records changed by this trial are shown.</p></div></div>
        {trial.changes.length === 0 ? <p className="muted">No state changed.</p> : trial.changes.map((change) => (
          <article className="state-change" key={`${change.resource}-${change.id}`}>
            <header><code>{change.resource}/{change.id}</code><span className="badge badge-neutral">{change.op}</span></header>
            <div className="state-columns">
              <div><span>Before</span><pre>{JSON.stringify(change.before, null, 2)}</pre></div>
              <div><span>After</span><pre>{JSON.stringify(change.after, null, 2)}</pre></div>
            </div>
          </article>
        ))}
      </section>
    </div>
  );
}
