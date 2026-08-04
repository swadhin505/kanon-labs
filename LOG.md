# Build log — Kanon Labs

Living status file. Updated at the end of every phase.
Plan it follows: [`V0-BUILD-PLAN.md`](./V0-BUILD-PLAN.md) · Why: [`WHAT-TO-BUILD.md`](./WHAT-TO-BUILD.md)

---

## Where we are

| | |
|---|---|
| **Last updated** | 2026-08-03 |
| **Phase done** | **Original plan Phases 0–6 complete: v0 build checkpoint** |
| **Phase next** | Independent review, then user-approved commit/push |
| **Tests** | `pytest -q` → **126 passed**, `ruff check .` clean (+1 live test deselected); UI lint/build clean |
| **SUTs** | OpenAI tool-calling agents plus a framework-neutral external adapter |
| **Measured live** | ✅ `gpt-5-mini`, 4 stories × 3 trials: **pass^3 1.00** green, and a real regression caught. |
| **Domains proven** | health-insurance · bank (second domain, zero product changes beyond one new pack feature) |
| **Core deps** | `pydantic`, `pyyaml`; HTTP/MCP and fuzzing stay optional extras |

**Build order note:** we inverted the plan. It said compiler first, gate second.
We do **gate first on a hand-written pack**, compiler after — so there is a
demoable red-CI run weeks earlier, and the compiler ships against a known-good
target artifact instead of a hypothesis.

### What works right now, in plain words

1. **A fake insurance company you can call.** Members, plans, claims. It
   remembers things between calls and refuses impossible ones — you cannot pay a
   claim nobody approved. Same answer every single time you run it.
2. **A rulebook it checks against.** Two rules so far: don't approve more than
   the plan covers, and look a member up before filing for them. These are the
   rules the fake company *can't* enforce by itself.
3. **Four test conversations.** File a claim · push for more than you're covered
   for · file for someone whose insurance lapsed · just check your claim status.
4. **Something that plays a robot through those conversations and grades it** —
   and when it fails, says which rule broke and at which exact moment.
5. **A check that the tests themselves are real.** Every scenario is also run
   against a robot that does nothing. If that still "passes", the scenario is
   testing nothing and gets flagged.

6. **A red/green verdict you can put in CI.** Save a run as the "last good"
   result, then re-run after a change. If any category got worse, the command
   exits non-zero and prints a comment naming the category, the story, and the
   rule that broke.
7. **A real AI agent in the seat.** An OpenAI tool-calling agent whose
   instructions live in a text file. The tools it can call are generated from
   the same YAML that defines the fake company, so it can never be offered an
   operation the fake company doesn't have.
8. **An OpenAPI 3 compiler.** It turns the operations an agent actually uses into
   the same behavior pack, keeps inferred state transitions in a separate review
   layer, and replays real traces to catch behavior that the twin got wrong.
9. **Users who actually reply.** Stories can push back or confirm a specific
   action mid-conversation. The scorer reads exact scenario metadata for
   confirmation, never guesses intent from words.
10. **An adaptive user when authored turns run out.** It is optional and uses an
    LLM only to continue the conversation; the scorer remains deterministic.
11. **HTTP + MCP and external-agent support.** A served twin exposes the same
    state through both transports. LangGraph, n8n, A2A, or custom-agent calls
    are audited and enter the scored trajectory without being replayed.
12. **Property-based OpenAPI conformance.** `kanon twin fuzz <domain>` generates
    valid and invalid requests for every served operation and validates every
    response with Schemathesis.
13. **A frontend for the evidence.** The health-insurance run now renders as a
    results overview, a worst-first regression diff, and a scenario drill-in
    with the transcript, tool request/response, state diff, and exact invariant
    violation step.

**V0 build plan complete.** The secondary experiment launcher and artifact
browser remain deferred follow-ons; they are not required by the frontend done
criterion.

### The demo, in two commands — with a real model

```
$env:MODEL_API_KEY = (Get-Content .secrets/openai-key.txt -Raw).Trim()
python -m kanon gate run data/health-insurance --agent llm        --trials 3 --save runs/llm-green.json
python -m kanon gate run data/health-insurance --agent llm-no-cap --trials 3 --baseline runs/llm-green.json
```

The only difference between the two agents is one block of text in a prompt file.
The first is green at **pass^3 1.00**; the second exits `1`:

```
### FAIL — health-insurance / llm-no-cap
pass@1 0.92 · pass^3 0.75 · 4 stories × 3 trials
baseline `llm`: pass^3 1.00 (-0.25 overall) — the aggregate is not where the story is

| slice                             | pass^k | change |    |
| file_claim / HI-P3 / adversarial  | 0.00   | -1.00  | 🔻 |
| check_status / - / cooperative    | 1.00   | +0.00  |    |
| file_claim / HI-P1 / confused     | 1.00   | +0.00  |    |
| file_claim / HI-P1 / cooperative  | 1.00   | +0.00  |    |

- hi-002 1/3 trials
  - payout_within_annual_cap: MEM-0001 approved for 36162.0 against the Gold annual cap of 25000
      step 5: approve_claim(claim_id='CLM-0002', approved_amount=36000) -> ok

- model calls spent on this run: 60
```

Three of four slices untouched. One collapsed. That is the failure that gets
found weeks later by a support ticket.

The scripted agents (`good` / `subtle` / `broken`) still exist and still run the
same demo for free, without an API key.

### Changelog

| Date | What landed |
|---|---|
| 2026-08-03 | **Referential-integrity boundary hardened** — reference declarations must name real source fields; seeded values are type-checked; effects must read and write declared numeric fields and cannot create dangling references; any unexpected write failure rolls back the complete call; snapshot restore rejects orphaned state and preserves the previous good state. The HTTP admin restore endpoint now returns the same structured twin error instead of a 500. 119 → 126 tests. |
| 2026-08-02 | **Phase 6 complete** — root README reproduces green baseline → one-slice red regression → dashboard from a clean copy; GitHub Actions runs backend tests, frontend build, the reliability gate, writes its Markdown to the job summary, and uploads the JSON evidence. Fixed the Windows CLI crash caused by emoji regression markers. |
| 2026-08-02 | **Phase 5 core frontend complete** — Next.js 15 App Router overview, per-slice regression view, copyable CI markdown, and scenario drill-in with trial switching, transcript/tool payloads, state diff, score components, and structured invariant evidence linked to the exact violating step. Saved reports now carry that evidence instead of only aggregate reasons. Added a real green/subtle fixture generated through the CLI. 109 → 110 tests; UI lint/build and production dependency audit clean. |
| 2026-07-31 | **Referential integrity (`references`)** — the twin used to accept a claim for a member that does not exist and return 200, which is the silent-tool-failure class the product exists to catch. A resource now declares `field -> resource`; writes are refused with `unknown_reference`, deletes with `reference_in_use`, and a pack whose *seed* is already orphaned fails to load. The compiler derives links from field naming into the review layer. Deliberately **not** built: Python hooks — nothing has forced them, and they would put arbitrary code back in the trust path. 109 → 119 tests. |
| 2026-07-31 | **Independent-review hardening** — authored confirmations now wait for an explicit successful tool marker and confirming turns cannot load without one; `--infer` without `-o` prints the review patch; request-only OpenAPI controls are accepted but never persisted into response records; effects cannot write another resource's id, state, or timestamps. Added exploit/regression checks for all four findings. 104 → 109 tests. |
| 2026-07-31 | **Vendor-neutral SUT boundary** — removed the unnecessary IBM-specific adapter. External agents now supply one `invoke(messages) -> text` callable while the shared HTTP/MCP twin audit log captures and scores their tool calls. This covers LangGraph, n8n webhooks, A2A clients, and custom frameworks without Kanon depending on any of them. |
| 2026-07-30 | **Pre-frontend checkpoint complete** — strict engine argument validation, richer OpenAPI request typing, HTTP+MCP serving with bearer protection, adaptive LLM user simulation, stable response-subset fidelity, separate safety-slice tolerance, safe policy predicate drafts, remote twin audit events, and Schemathesis valid+invalid fuzzing. Health insurance: 220 generated calls; bank: 120. 93 → 104 tests. |
| 2026-07-30 | **Phase 5c, active user simulator** — story-authored user turns now enter the real agent conversation; adversarial users push back before the decision. Added exact action-specific confirmation metadata and `confirm_before_paying`, with a new multi-turn payment slice. Good agents stay green; the broken agent is cited at the premature `pay_claim`. 90 → 93 tests. |
| 2026-07-30 | **Phase 5b hardening audit** — fixed false coverage for missing tools, path-level parameters, referenced request bodies, YAML input, all successful 2xx responses, `allOf`, stale inferred layers, human overrides during fidelity, uncovered-operation false greens, and incomplete create-state mappings. Real GitHub scope improved 3/6 → 4/6; Slack Swagger 2 now fails explicitly as outside the OpenAPI 3 v0 scope. 82 → 90 tests. |
| 2026-07-29 | **Phase 5b complete** — transition inference (`--infer`) into its own reviewable layer, and the fidelity gate (`--traces`) that certifies it. Verified end to end on real Stripe: correct machine inferred, gate green, exit 0. The gate found two structural holes while I built it. 69 → 82 tests. |
| 2026-07-29 | **Phase 5b, deterministic half** — `kanon/compile/openapi.py`, `kanon twin compile`, RFC 7386 layered packs, `Route.id_param`. Verified on the real 7.9 MB Stripe spec: 7/7 of a 9-tool scope, 0.68s. 54 → 69 tests. |
| 2026-07-27 | **First live measurement** (`gpt-5-mini`, ~340 model calls total). Tuned the prompt from pass^3 0.50 to 1.00 in three measured steps, then caught a real regression. See §5. |
| 2026-07-27 | **Phase 5a** — a real OpenAI tool-calling agent under test, tool schemas generated from the pack, prompts as files. Found and fixed: tool arguments were over-exposed (`submit_claim` could set the payout). 48 → 54 tests. |
| 2026-07-27 | **Phase 4** — pass^k per slice, regression vs. a saved baseline, the CI verdict and markdown comment, `Domain` loader, agents moved into the domains. 34 → 48 tests. |
| 2026-07-27 | **Generality test** — a second domain (`data/bank/`) with no insurance in it. Found and fixed two real holes: one operation can now change several records (`effects`), and writes are atomic. 29 → 34 tests. |
| 2026-07-27 | **Phase 3** — scenarios, the turn loop, the state diff, pass/fail with reasons, the do-nothing baseline. `story.py` `scorer.py` `runner.py` `stories.yaml`. 19 → 29 tests. |
| 2026-07-27 | **Phase 2** — the rulebook: trajectory type, rule registry, two health-insurance rules. `trajectory.py` `invariants.py` (+ the domain's own `invariants.py`). 12 → 19 tests. |
| 2026-07-27 | **Phase 0** — closed the one real gap: a command line entry point. `cli.py`. 10 → 12 tests. |
| 2026-07-27 | **Phase 1** — the twin: store, behavior pack, engine, health-insurance example. 0 → 10 tests. |

### Phase 0 ledger — what was done, skipped, and voided

Phase 1 got built before Phase 0 was formally closed. Going back over it:

| Plan item (§4) | Status |
|---|---|
| Runnable skeleton, `pytest -q` green | ✅ done — real tests, not the planned empty smoke test |
| `twin build <pack>` runs clean | ✅ done — `python -m kanon twin build …`, and it validates for real rather than printing `TODO` |
| Pin every proposed dependency | **re-scoped.** FastMCP and Schemathesis are optional extras because they are now used. `openapi-core`, datamodel-code-generator, and agentevals remain unnecessary for the implemented pack-level design. |
| Vendor tau2 into `gate/_tau2/` | **voided** — we're not vendoring the orchestrator (§3) |
| Confirm pipeline artifacts for health-insurance | **deferred, deliberately.** Greenfield, so they must be hand-authored. `policies.yaml` is Phase 2's input, `stories.yaml` is Phase 3's, `api.json` is Phase 5's. Each gets written by the phase that consumes it, not a phase early. |

Net: one real gap (no entry point), now closed. Everything else was either done
or deleted on purpose.

---

## 1. Folder structure

```
kanon-labs/
├── kanon/                      # the product
│   ├── __init__.py             #   states the two-time-zone rule
│   ├── __main__.py             #   `python -m kanon …`
│   ├── cli.py                  #   entry point (argparse, no dependency)
│   ├── domain.py               #   loads one customer's world from a directory
│   ├── twin/                   #   RUN TIME — deterministic. no LLM, no clock, no RNG.
│   │   ├── __init__.py
│   │   ├── store.py
│   │   ├── pack.py
│   │   └── engine.py
│   ├── sut/                    #   the agent being MEASURED — the only run-time LLM
│   │   ├── __init__.py
│   │   ├── tools.py            #     pack -> tool schemas (provider-neutral JSON Schema)
│   │   └── llm.py              #     OpenAI tool-calling loop
│   └── gate/                   #   RUN TIME — deterministic scoring. same rule.
│       ├── __init__.py
│       ├── trajectory.py       #     what happened during a run
│       ├── invariants.py       #     the rule registry
│       ├── story.py            #     scenarios + their expectations
│       ├── scorer.py           #     state diff -> pass/fail with reasons
│       ├── runner.py           #     the turn loop, k trials, no-op baseline
│       ├── metrics.py          #     pass^k per slice + the saved run report
│       ├── regression.py       #     this run vs the last green one
│       └── ci.py               #     the verdict: exit code + PR comment
│
├── data/                       # EXAMPLES ONLY. no product code lives here,
│   │                           # and no domain knowledge lives outside here.
│   ├── health-insurance/
│   │   ├── pack.yaml           #   members, plans, claims + the claim lifecycle
│   │   ├── invariants.py       #   the two insurance rules
│   │   ├── stories.yaml        #   four insurance conversations
│   │   ├── prompts/            #   support.md + support-no-cap.md — the staged regression
│   │   └── agents.py           #   scripted (good/subtle/broken) + real (llm/llm-no-cap)
│   └── bank/                   # the generality test — deliberately unlike insurance
│       ├── pack.yaml           #   accounts, transfers, money moving between them
│       ├── invariants.py
│       ├── stories.yaml
│       └── agents.py
│
├── tests/
│   ├── test_twin.py
│   ├── test_cli.py
│   ├── test_invariants.py
│   └── test_gate.py
│
├── pyproject.toml
├── LOG.md                      # this file
├── V0-BUILD-PLAN.md            # phase plan
├── WHAT-TO-BUILD.md            # research + why this product
└── agentune.md                 # the upstream pipeline this builds on
```

**Folder still to come before packaging:**

| Folder | When | What goes in it |
|---|---|---|
| `ui/` | Phase 5 | Next.js dashboard |

The trust boundary is functional: twin execution and scoring are deterministic.
LLMs may infer build-time behavior, act as the SUT, or play the simulated user,
but they never decide pass/fail.

---

## 2. What each file does

### `kanon/twin/store.py` — the records
Plain `records[resource][id] -> dict`. No database.

- **Deterministic ids** — a per-resource counter (`CLM-0002`), never a uuid. On
  load it reads the counter past the seeded ids so new records can't collide.
- **Logical clock** — `step` counts writes. Timestamps are derived from it, so a
  run's output depends only on what the agent did, never on the wall clock.
- **Reads always copy** — `get`/`list`/`state` return deepcopies, so a caller
  can't reach in and mutate the store by accident.
- **`snapshot()` / `restore()`** — deepcopy of records + counters + step. This is
  the control plane; it's free because we own the store.

Key names: `Store`, `Snapshot`, `Record`, `MissingRecord`.

### `kanon/twin/pack.py` — the behavior pack schema
The declarative artifact a spec compiles into. **Data, not generated code** — so
a wrong rule is a YAML edit, not a redeploy. That's the calibration knob.

- `Resource` — one collection: `id_field`, `id_prefix`, `state_field`,
  `transitions` (the legal state machine), `timestamps`, `seed`.
- `Route` — binds one **operation id** (a tool the agent can call) to a verb on a
  resource: `verb`, `requires`, `filter_by`, `sets_state`.
- `Pack` — `name` + resources + routes, loaded via `Pack.from_yaml(path)`.

**Everything is validated on load.** A typo'd transition target (`aproved`) would
otherwise read as a terminal state and silently turn a legal move illegal — the
exact generated-pack bug that shows up as a mystery test failure weeks later.
Bad pack ⇒ `ValueError` naming the field, at build time.

Schema shape borrows from mockd (`tables`/`extend`) and Mockoon CRUD routes —
the CRUD half is commodity. `transitions` is the part they don't have.

### `kanon/twin/engine.py` — the interpreter
Written **once**, works for any pack. One entry point:

```python
twin.call(operation_id, args) -> record | list[record]
```

- Dispatches by verb: `list`, `read`, `create`, `update`, `delete`.
- **Enforces the state machine.** `pay_claim` on a claim that was never approved
  is a `409 invalid_transition`, and nothing is written. This is the difference
  between a twin and a mock.
- **State is only settable through a route.** A caller passing
  `{"status": "paid"}` gets it silently dropped — exactly one source of truth per
  transition.
- **Strips server-owned fields** from caller input (`id_field`, `state_field`,
  `timestamps`). No agent can forge an id or a timestamp.
- **Never fakes.** An operation the pack doesn't cover raises `501` *and* is
  counted in `twin.uncovered`. Silent stubs are how an eval scores 100% on
  nothing; that's the one sin we don't commit.
- Control plane: `reset()`, `snapshot()`, `restore()`, `state()`.
  `state()` is what the scorer will diff.

Key names: `Twin`, `TwinError` (`code`/`message`/`status`, `.as_response()`),
`DEFAULT_EPOCH`.

### `kanon/cli.py` — the entry point
`python -m kanon twin build <pack>` (or `kanon twin build …` after
`pip install -e .`). Validates the pack, then prints the twin it produces:
resources with seeded counts and their state machines, and the operation ids the
agent will be able to call. Exit 1 with the offending field on a bad pack.

Uses stdlib `argparse` — one command with one argument doesn't need typer.
It deliberately prints **no coverage percentage**: coverage means "of the spec's
operations", and the spec doesn't enter until the compiler. A number we can't
compute honestly is worse than no number.

`kanon/__main__.py` is a three-line shim so it runs without installing.

### `data/health-insurance/pack.yaml` — the worked example
3 resources (`plan`, `member`, `claim`), 11 routes. The claim FSM is
`submitted → under_review → approved → paid`, with `denied` and `paid`
terminal. `plan.annual_cap` exists so Phase 2 has a real cap invariant to check.

Hand-written on purpose: this is the **target output** the compiler will learn to
emit in Phase 5.

### `tests/test_twin.py` — the runnable check
10 tests, one per promise the engine makes: state persists · illegal transition
refused and nothing written · terminal states are terminal · caller-supplied
state ignored · snapshot/restore round-trips (including the id counter) ·
uncovered op flagged not faked · missing arg / missing record · list filtered and
ordered · reads can't mutate the store · a bad pack fails at load.

### `kanon/gate/trajectory.py` — what happened during a run
One flat, ordered list of events: `Message(role, content)` and
`ToolCall(operation, args, result, error)`. A position in that list is a
**step**, and a step is what a failure cites.

Flat and ordered on purpose: most interesting rules are about order — "the agent
paid before anyone confirmed" is a claim about where a message sits relative to
a call. Helpers: `calls(operation)`, `messages()`, `describe(step)`.

### `kanon/gate/invariants.py` — the rule registry
The framework only; no domain knowledge lives here.

- `Violation(rule, message, step)` — one breach, with the step to blame and a
  `render()` that prints the offending line underneath it.
- `@invariant(name, description=…, policy=…)` — registers a predicate
  `fn(state, trajectory) -> list[Violation]`. Empty list means it passed.
- `load(path)` — imports a customer's rule file by path. This is how "works for
  *your* APIs" stays true: their rules ship beside their data, not in our
  package. Loading twice is a no-op.
- `check(state, trajectory, names)` — runs them. An unknown rule name **raises**;
  a rule that silently stops being checked is worse than no rule.

Python predicates, not Rego/JSONLogic: ordering and cross-referencing over a
trajectory need real code. CEL is the documented upgrade path for the day
non-engineers author rules as data.

### `data/health-insurance/invariants.py` — the domain's two rules
Both are things the twin **cannot** enforce by itself:

- `payout_within_annual_cap` (state) — a member's total approved payout vs. their
  plan's cap. Cross-resource: claim → member → plan. If a payout can't be
  attributed to a plan at all, that is reported as a finding, not passed.
- `verify_member_before_claim` (trajectory) — no `submit_claim` for a member
  before a *successful* `get_member` for them. Pure ordering; a lookup that 404'd
  proves nothing.

### `kanon/gate/story.py` — the scenarios
A `Story` is the unit everything is organised around: an `intent`, a `persona`,
an optional `policy`, the user's `goal` in words, and what must be true
afterwards. `story.slice` is `(intent, policy, persona)` — the grouping Phase 4
reports on.

Expectations are written against the **diff**, not a golden state dump:

| bucket | meaning |
|---|---|
| `expect` | these changes must happen |
| `allow` | these changes may happen |
| everything else | **forbidden — default deny** |

Default-deny is only affordable because the twin is deterministic: a story can
name `CLM-0002` before it exists, because ids are counted, not generated. That's
the payoff for banning uuids and wall clocks.

Also `must_call` — operations that must have been called *successfully*. This is
the trajectory half of the check, and it's what stops a read-only story from
being passed by an agent that sits still.

### `kanon/gate/scorer.py` — pass or fail, and why
`reward = state_ok × calls_ok × invariants_ok`. Multiplicative: 1.0 or 0.0, no
partial credit. Every failure appends a plain-English line to `reasons`.

`diff(before, after)` returns `Delta`s (`created` / `changed` / `deleted`, with
per-field before→after), ordered so two runs are comparable. This is the part
tau2 doesn't have — it hashes the final DB and compares, which can only ever say
"different". A hash can't tell you *which record* moved, and can't express "this
change was tolerable".

### `kanon/gate/runner.py` — playing a story
The runner owns the loop. The agent only decides *what to do next*; it never
touches the twin, so a script and an LLM produce identically-shaped trajectories.

- `Agent` protocol: `start(story)` + `next_action(trajectory) -> Call | Say | None`.
- `ScriptedAgent` — fixed actions per story. The stand-in until an LLM agent
  exists, and how a regression gets staged deliberately in the demo.
- `NullAgent` — does nothing.
- `play()` — one attempt: reset, loop, score. `max_steps` (50) means a confused
  agent produces a **failure with a reason, never a hang**.
- `run_story(twin, story, agent, trials=k)` — k attempts, plus the no-op baseline
  once. Sets `trivially_passed` on the result if the do-nothing agent also passed.

### `data/health-insurance/stories.yaml` — 4 scenarios
`hi-001` file a claim, cooperative · `hi-002` push for more than the cap allows,
adversarial · `hi-003` lapsed member, confused (nothing should be written) ·
`hi-004` read-only status check. Two intents and three personas, so Phase 4 has
real slices to group by.

### `tests/test_cli.py`
2 tests: a valid pack is described and exits 0; a pack with a typo'd transition
target exits 1 and names the problem on stderr.

### `tests/test_invariants.py`
7 tests: rules register with their policy id · a clean run passes · an over-cap
payout is caught **and cites the exact step** · filing without a lookup is caught
· a failed lookup doesn't count as verification · an unattributable payout is
reported not ignored · an unknown rule name raises.

### `kanon/sut/` — the agent under test
The only run-time LLM in the product, and it sits on the side being *measured*.
Delete this package and the twin and the gate still work.

- `tools.py` — pack → tool schemas. Provider-neutral JSON Schema; one wrapper
  function adds the OpenAI shape. The agent is offered **exactly** the operations
  the pack declares, with the right types (`amount: 800`, not `"800"`), and
  **only the arguments each operation should take** — filing a claim cannot set
  the payout amount.
- `llm.py` — `LLMAgent`, implementing the same two methods `ScriptedAgent` does.
  The runner still owns the loop, so a scripted agent and a real one produce
  identically-shaped trajectories. Handles parallel tool calls (all results go
  back together, in order), feeds the twin's **error bodies** back so the agent
  can react to a refusal, caps assistant turns, and treats malformed tool
  arguments as a visible failure rather than a crash.

The model id and the max-turn cap are config, not constants — set per agent in
the domain's `agents.py`. Prompts are files in `data/*/prompts/`, so a regression
is staged by editing a prompt, which is the real customer workflow.

### `kanon/domain.py` — one customer's world
A domain is a directory: `pack.yaml` + optional `invariants.py`, `stories.yaml`,
`agents.py` (which exports `AGENTS = {name: agent}`). `Domain.load(dir)` returns
all of it. The CLI and every test go through this, so there is exactly one way
to answer "what does this customer's setup look like".

Agents live in the domain, not in the tests. That's what makes the CI demo real:
the thing the demo grades and the thing the tests grade are the same object.

### `kanon/gate/metrics.py` — pass^k and the saved report
- `pass_hat_k(trials, successes, k) = C(successes, k) / C(trials, k)` — three
  lines copied from tau2, not the module they live in (that one also drags in
  pandas, an auth classifier, and LLM-judge error tagging).
- `StorySummary` / `SliceMetrics` / `RunReport` — per-story results grouped by
  slice, with `pass@1` and `pass^k` for each. **The gap between the two numbers
  is the point**: 4-of-5 successes is 0.80 at pass@1 and 0.00 at pass^5.
- `RunReport.save()` / `.load()` — plain JSON. That file is the green baseline.

### `kanon/gate/regression.py` — this run vs. the last green one
`compare(baseline, current, k)` → per-slice `SliceDelta` with a status
(`regressed` / `improved` / `flat` / `new` / `gone`), **sorted worst first**. No
baseline means every slice is `new`: a first run can't regress but still reports
its numbers.

### `kanon/gate/ci.py` — the verdict
`evaluate()` returns pass/fail plus reasons; `markdown()` renders the PR comment.
Three ways to fail:

1. **a slice regressed** beyond `--max-drop` (default: any drop at all)
2. **a story is trivially passable** — the do-nothing agent passes it, so it's
   inflating the score
3. **the twin was asked for something it doesn't cover** — the run happened in a
   world with a hole in it

Only (1) is a regression. (2) and (3) are the honesty rules, and they're hard
failures on purpose: a green 100% resting on either of them is precisely the
failure mode this product exists to refuse.

### `data/bank/` — the generality test
A second domain, written to stress the engine rather than to be pretty: accounts
with balances, and transfers that move money **between two accounts in one
call**. Insurance could never exercise that, because every insurance operation
touches exactly one record.

It found two real holes, both now fixed:

1. **One operation could only change one record.** Added `effects` to a route —
   declarative arithmetic on other resources (`add` / `subtract` / `set`), with a
   `min` floor that refuses the operation. That's how "insufficient funds" and
   "out of stock" are expressed without writing code.
2. **Writes weren't atomic.** A transfer that debited one account and then failed
   to credit the other would leave money destroyed. Every write now rolls back on
   any refusal.

### `tests/test_bank.py`
5 tests, and the file that fails first if insurance ever leaks into the engine:
money moves between two records in one call · the bank refuses an overdraft ·
**a refused transfer moves nothing at all** · an effect on a missing account
rolls the debit back · the whole gate runs on a domain it has never seen.

### `tests/test_gate.py`
10 tests, built around two agents — `GOOD` and `BROKEN`, the same agent before
and after a prompt change that dropped "check the cap" and "look the member up".

The diff reports creates/changes/deletes · unexpected changes fail by default ·
allowed changes are tolerated but not required · the good agent passes all four
stories · five trials agree (the environment can't be the source of variance) ·
the broken agent fails **for the stated reason**, with the failing step cited ·
an agent that never stops fails rather than hangs · a story stripped of its
`must_call` is detected as trivially passable · slices parse · duplicate story
ids are rejected.

### `tests/test_regression.py`
11 tests. The centrepiece is `test_a_collapsed_slice_is_invisible_in_the_aggregate`:
four slices, one dies completely, three improve, **the headline number is
identical before and after** (0.6875 both times) and only the per-slice table
shows the corpse. Plus: pass^k is worst-case not best-case · a tolerated wobble
doesn't fail the build · improvements never fail · each of the three honesty
rules turns the build red · a report round-trips to disk unchanged.

### `tests/test_cli.py`
6 tests, and the end-to-end demo lives here: build a twin · record a green
baseline · run the blunt regression and check the report names what broke · run
the **subtle** regression and assert exactly one slice moved · a first run can't
regress · an unknown agent errors.

Run everything: `pytest -q` from the repo root. Lint: `ruff check .`

---

## 3. Decisions made (and what they cost)

| Decision | Why | Cost if wrong |
|---|---|---|
| **Keep HTTP/MCP outside the engine.** `Twin.call()` remains the one interpreter; `kanon/serve.py` is a thin optional transport. | Local and external agents use the exact same state machine without framework dependencies. | Add transport-specific behavior only when a recorded trace proves it is required. |
| **Don't vendor tau2's orchestrator.** | With no host repo it drags `registry.py`, `data_model/`, `environment/` and litellm along. A turn loop is ~120 lines. | We rewrite ~120 lines. We still copy tau2's user-simulator *prompt*, which is the researched part. |
| **Copy `pass_hat_k`, not the metrics module.** | The formula is 3 lines; the module is pandas + auth classifiers + LLM-judge tagging. | None. |
| **Behavior pack is data, not codegen.** | A wrong rule is a YAML edit. Hand-editable = the calibration knob the domain needs. | If YAML can't express a rule, add a Python hook escape hatch (planned, not built). |
| **Use Pydantic + Schemathesis instead of openapi-core.** | FastAPI validates generated request/response models; Schemathesis exercises both valid and invalid contracts. | Re-add openapi-core only if conformance against an original provider path/parameter layout cannot be expressed by the generated transport. |

**Rejected after checking:** Microcks stateful mocks (Groovy scripts, string K/V
store, **10-second default TTL**) · Prism (stateless) · Mockoon (Node +
Handlebars for any logic) · mockd (Go binary, no invariants, cross-process — but
we stole its config shape).

---

## 4b. What the first live run found

Four measured runs against `gpt-5-mini`, 4 stories × 3 trials each (60 model
calls per run). Every number below is real, not illustrative.

| Run | Prompt change | pass@1 | pass^3 | What broke |
|---|---|---|---|---|
| 1 | *(starting prompt)* | 0.83 | 0.50 | paid a claim unasked (1/3); breached the cap (2/3) |
| 2 | + don't pay, + procedural cap | 0.92 | 0.75 | approved the full bill, no coinsurance (2/3) |
| 3 | + coinsurance rule | 0.83 | **0.75** | applied coinsurance, **forgot the cap** (2/3) |
| 4 | cap + coinsurance as one `min()` | 1.00 | **1.00** | — green |

Findings worth keeping:

- **Runs 2 and 3 have the identical headline pass^3 of 0.75 and completely
  different failures.** One slice was fixed and another broken in the same edit.
  The aggregate could not tell them apart; the per-slice table could. This is the
  product's core claim, observed rather than argued.
- **The scenario was wrong before the agent was.** hi-001 expected a payout of
  720 (800 minus 10% coinsurance) while the prompt never mentioned coinsurance —
  the *first* model run had inferred it from the plan record, which is what made
  the expectation look reasonable. A scripted agent can never surface this,
  because the script and the expectation were written by the same hand.
- **Two rules stated separately get applied separately.** The model would apply
  coinsurance *or* the cap, not both, until they were combined into a single
  `min(covered, remaining)`. Splitting the rule was the bug.
- **The twin blocked a cover-up.** In run 1 the agent over-approved, noticed, and
  tried to re-approve at the correct amount. The state machine refused — an
  approval cannot be changed. A mock would have accepted the second write and the
  final state would have looked clean.
- **Failure attribution had a bug, and this exposed it.** The cap rule was citing
  the *refused* correction attempt rather than the approval that caused the
  breach. Fixed: it now looks for the last **successful** approval.
- Non-determinism is real and worth measuring: identical prompt, twin and story,
  and the first two single-trial runs of hi-001 disagreed. That is what pass^k is
  for, and it was inert until a real model was in the seat.

---

## 4e. Phase 5b — inference, and the gate that certifies it

Two modules, and the second is what makes the first safe to trust.

```
python -m kanon twin compile spec.json --tools "..." -o pack.generated.yaml \
    --infer --traces traces.yaml
```

**End-to-end on the real Stripe spec:** correct machine inferred
(`pending → [succeeded, failed]`, create sets `pending`, capture sets `succeeded`),
fidelity gate green, exit 0. One model call.

### `transitions.py` — the only genuine guess, fenced on both sides

*Before:* the **states are not guessed.** They come from the spec's own `status`
enum, which the deterministic pass already found. The model is only asked which
edges connect them.

*After:* every claim is validated against the spec. Invented states, unknown
operations, and `sets_state` values outside the enum are **rejected and reported**,
never merged. Output goes to `pack.inferred.yaml` — its own layer, so it can be
diffed, edited over, or deleted wholesale to reject every guess.

### `fidelity.py` — replay recorded traces, compare accept vs refuse

Body comparison would be theatre: our ids are counted, our clock is logical, and a
real response has dozens of fields we never model. But **accept-vs-refuse** is
exactly what an inferred state machine gets wrong, and exactly what a recording
settles:

| | means |
|---|---|
| real API accepted, twin refused | the pack is too strict → phantom failures |
| real API refused, twin accepted | too permissive → **false green**, the dangerous direction |

Traces are *sequences*, because a transition can only be tested by reaching it:
capture-after-authorise must pass, capture-twice must not.

### Two structural holes the gate found while I was building it

Both are the same species — a state machine that looks like coverage and enforces
nothing — and neither was reachable from the fixture:

1. **Transitions with no `sets_state` anywhere.** The engine only checks transitions
   on routes that declare one. A pack can carry a full machine and enforce nothing.
2. **No create operation sets an initial state.** On the real Stripe run the model
   mapped `capture` but not `PostCharges`, so every charge was born with no status
   and the *first* transition failed. A machine nothing can enter is not a machine.

Both now drop the resource entirely with a stated reason, rather than shipping a
machine that checks nothing. The prompt was also strengthened to demand the create
mapping — but the guard is what makes it safe, not the prompt.

**Third find, from reading a debug dump:** Stripe's descriptions are **raw HTML**
(`<p>`, `<a href>`, `<code>`, entities). Those flow into the tool schemas the agent
under test reads *and* into the inference prompt. Now stripped.

### Provenance is three files, not annotations

```
pack.generated.yaml   read out of the spec      -- trustworthy
pack.inferred.yaml    guessed by an LLM         -- REVIEW, or delete to reject
pack.yaml             written by a human        -- always wins
```

Merged in that order by RFC 7386. Neither machine step writes the file your edits
live in.

---

## 4d. Phase 5b — the compiler's deterministic half

`kanon/compile/openapi.py`. **No LLM.** Spec + the agent's tool list → resources,
routes, field types, descriptions. Parsing, not inference — it either works or it
raises, and it is tested offline.

```
python -m kanon twin compile spec.json --tools "GetCharges,PostCharges,..." -o pack.generated.yaml
```

Verified against the **real 7.9 MB Stripe spec**: a 9-tool agent scope compiled
**7/7 expressible** in 0.68s, the other 2 correctly flagged.

**The one rule that makes it work: resource identity comes from the response
schema, never the URL.** The engine routes by `operation → resource + verb`, so:

| Operation | Returns | → |
|---|---|---|
| `POST /charges/{charge}/capture` | a `charge` | `update` on charge |
| `POST /accounts/{account}/people` | a `person` | `create` on person, `account` required |

Same URL shape, different verb, decided by what comes back. Path-shape
classification gets both wrong — that was the probe's whole lesson.

**Two bugs the real spec found that the fixture never would have:**

1. `GET /v1/customers/{customer}` returns `anyOf: [customer, deleted_customer]` —
   a live-or-deleted union. Reading only a direct `$ref` reported it *uncovered*,
   putting a hole in the twin for one of the commonest resources. My first fix
   found the name but compiled **zero fields**, because `_deref` hands back the
   `anyOf` wrapper (truthy, no `properties`). Both halves are now pinned in
   `test_compile.py`.
2. Stripe names the same resource's id `{charge}` in one operation and `{id}` in
   another. The argument name is per-operation, the record key is per-resource —
   they are not the same thing. Added `Route.id_param`, and the engine strips it
   so the param name never leaks into the stored record.

**Hand edits are structurally safe.** Two files, merged by
[JSON Merge Patch (RFC 7386)](https://datatracker.ietf.org/doc/html/rfc7386) —
recursive merge, `None` deletes:

```
pack.generated.yaml   compiler owns it, rewrites it freely, never hand-edit
pack.yaml             you own it, sparse, merged on top, always wins
```

Chosen over the [OpenAPI Overlay Specification](https://spec.openapis.org/overlay/v1.1.0.html)'s
JSONPath actions because a pack is three levels deep — you write the subtree you
want changed. Overlay-style targeting is the documented upgrade path if anyone
needs wildcards. (Overlay has no Python implementation anyway; RFC 7386 is 15
lines and no dependency.)

**Honest about what it doesn't do:** `transitions` are empty (no spec has them —
that's the LLM pass), optional arguments aren't populated (not derivable; `accepts`
is a hand edit), `seed` is empty (a spec describes shape, not data). All three are
printed as review notes on every compile, and free-text search plus binary uploads
come back as `Uncovered` rather than silently faked.

---

## 4c. Coverage probe — do real specs fit the pack model? (research, no code)

Ran the pack's five verbs against three real downloaded specs before building the
compiler. Throwaway script, deleted afterwards; the repo did not change.

| Spec | Operations | Raw "unmapped" by URL shape | **Actually a gap** |
|---|---|---|---|
| Swagger Petstore | 19 | 11% | ~10% (file upload) |
| Twilio Messaging v1 | 58 | 21% | small |
| Stripe (`spec3.json`, 7.9 MB) | 587 | 23% | **~1–2%** |

**The first pass was wrong and the correction is the finding.** Measuring by URL
shape said 23–41% of a real spec doesn't fit, which would have justified a big
redesign. But `Twin.call(operation, args)` routes by **operation name → resource
+ verb** — no path template is involved. Checked against the actual spec:

| Stripe operation | Returns | Keyed by | Our equivalent |
|---|---|---|---|
| `POST /charges/{charge}/capture` | a `charge` | its own id | `pay_claim` (update + sets_state) |
| `POST /application_fees/{id}/refund` | an `application_fee` | its own id | same shape |
| `POST /accounts/{account}/reject` | an `account` | its own id | `deny_claim` |
| `POST /accounts/{account}/people` | a `person` | parent `account` | `submit_claim` (requires `member_id`) |

Every "action verb" I flagged as a hole is already expressible. The irreducible
gap across all three specs is narrow and nameable:

- **query-DSL search** (`GET /customers/search?query=...` — free text, not our
  equality `filter_by`)
- **binary / multipart upload** (`POST /pet/{id}/uploadImage`)

Also confirmed: only 4 of Stripe's 1431 schemas use `oneOf`/`anyOf`/`allOf`/
`discriminator`, so polymorphism is not the obstacle it looked like either.

**Consequences for 5b:** the pack schema needs **no sixth verb**. The compiler's
job is smaller than planned — path params become required arguments, search and
upload operations get **flagged uncovered rather than guessed at**, and the LLM's
role narrows to the one thing no spec contains: transitions.

---

## 4f. Phase 5 — frontend evidence surface

The plan's three checkpoint screens are in `ui/`:

- `/` shows aggregate pass^k, the worst slice, regression count, deterministic
  call coverage, policy coverage, and a clickable per-scenario heatmap.
- `/regression` compares the current run to the last green run, keeps the worst
  delta first, states the CI verdict, and copies the same result as Markdown.
- `/scenarios/[id]` shows each trial's ordered messages and tool calls, request
  and mocked response, deterministic/stubbed provenance, changed twin records,
  scorer components, and every policy check. A violation links to the exact
  trajectory step that caused it.

This required one backend contract improvement: `RunReport` now persists
structured trial evidence (events, state changes, and invariant violations)
alongside the existing aggregate summary. Old report JSON still loads because
the detail field is optional.

**Deliberate dependency cut:** the original manifest listed shadcn, Tailwind,
TanStack Table/Query, Recharts, and react-flow. None solves a problem on these
three static result screens. The implementation uses server components,
semantic tables, native `details`, and a CSS grid heatmap. Those libraries stay
deferred until the experiment launcher, trends, or tool-graph screen makes one
necessary.

The checked-in `ui/data/{baseline,current}.json` files are not invented fixture
shapes. They were produced by the real CLI from the `good` and `subtle` agents,
three trials per story. The current artifact fails only `hi-002`, with
`payout_within_annual_cap` linked to step 6.

Verification: Next.js 15.5.22 production build and ESLint pass; all four local
routes return 200 and the failing drill-in contains its invariant and `step-6`;
`npm audit --omit=dev` reports zero vulnerabilities. The local environment had
no controllable browser available, so rendered screenshots were not inspected
in this checkpoint.

## 4g. Phase 6 — package and demo

`README.md` is the clean-checkout path: install the Python package, validate the
twin, save a green run, run the deliberately subtle regression, and open the
same two JSON artifacts in the dashboard. No model key is required.

`.github/workflows/kanon.yml` does the same work in CI. It preserves the gate's
real exit code while teeing the Markdown into GitHub's job summary, then uploads
the saved run report and summary as one artifact. It uses only GitHub's official
checkout/setup/upload actions.

Verified from `C:\tmp\kanon-clean-20260802-phase6`, copied without local caches,
secrets, virtual environments, node modules, or build output: fresh editable
Python install, twin build, green baseline, expected exit-1 regression with the
JSON still saved, fresh `npm ci`, and production dashboard build all passed.
The workflow YAML was parsed independently before this run.

---

## 4. Things we found that change the design

- **tau2 has no `db_diff`.** Its evaluator is plain `get_db_hash()` equality.
  So the readable 3-bucket state diff (expected / allowed / **forbidden**) is
  entirely ours to write, not an "extension". Re-scoped into Phase 3.
- **tau2's own docs admit the no-op hole:** for airline task 1, *"an agent that
  does nothing but politely refuse will receive full reward 1.0."* → We will run
  every scenario once against a **null agent**; any scenario it passes is invalid
  and gets flagged, not counted. ~40 lines, and nobody ships it.
- **The clean integration seam is `Environment`** (`make_tool_call` +
  `get_db_hash`) if we ever do adopt tau2 modules. Worth remembering.
- **Agent Tune is not public and we have no copy** → greenfield. The pipeline
  artifacts (tool graph, policies, stories) get hand-written for
  health-insurance until there's a reason to generate them.

---

## 5. Deferred, tracked

Each of these is a `ponytail:` comment in the code, not a forgotten idea.

| Where | Shortcut | Upgrade when |
|---|---|---|
| `store.py` | whole-store deepcopy on snapshot | it shows up in a profile → copy-on-write overlay |
| `engine.py` | one generic error envelope | a real spec's error shape breaks an agent → `errors` section in the pack |
| — | no pagination / rate limits / auth in the engine | a policy or story needs one |
| — | no Python hook escape hatch for logic YAML can't express | a sample proves the declarative pack insufficient |

---

## 6. Decisions taken, and what's still open

**Settled 2026-07-27:**

- **hi-002's correct outcome is "approve up to the remaining allowance"**
  (confirmed with the user), not a flat refusal. Chosen partly because it gives
  the scenario something real to check — was the arithmetic right? — where a
  refusal only checks that nothing happened.
- **`confirm_before_paying` uses structured scenario metadata.** The user still
  sends ordinary chat text, but the authored turn tags exactly which operation
  and record it confirms (for example `pay_claim:CLM-0002`). The scorer checks
  order and identity without keyword matching or an LLM judge.
- **A story with `invariants: []` still checks nothing** — but Phase 4's CI
  summary will report the count, so it's visible rather than silent.
- **`allow` stays** even though no story uses it yet. Three lines.

**Still open:**

- No blocking code gap remains after the integrity review. Visual browser QA
  still needs a machine with an available browser, and the GitHub workflow can
  only be observed after the user-approved commit/push.

---

## 7. Next up

**Cost guards, since a run is stories × trials × turns:**

| Guard | Default | Scope |
|---|---|---|
| `max_turns` | 10 | one trial — stops a single conversation looping |
| `max_calls` | 150 | the whole run — the one that protects the bill |
| runner `max_steps` | 50 | coarse backstop, any agent |

Exhausting the run budget fails the remaining stories loudly rather than quietly
passing them — a truncated run must never look like a clean one. Secrets stay in
environment variables or `.secrets/`, which is gitignored.

**No planned build phase remains.** Next is independent review of the
uncommitted checkpoint, then commit/push only when the user approves it.

### Known gaps worth naming

- **Four stories is a small suite.** The subtle-regression demo still moves the
  aggregate by 0.25, because one of four slices is 25% of everything. The claim
  "the aggregate hides it" is properly demonstrated in
  `test_a_collapsed_slice_is_invisible_in_the_aggregate`, where the headline is
  *identical* before and after. On a real 200-story suite the demo would look
  like the test, not like the demo.
- **Fuzzing validates the generated twin contract, not every provider-specific
  wire detail in the original OpenAPI document.** Recorded trace replay remains
  the fidelity gate for real provider behavior and stable response fields.
- **Automated frontend verification is green, but visual browser QA is still
  pending.** This environment exposed no controllable browser. ESLint and the
  production Next.js build both pass, and `npm audit` reports zero known
  vulnerabilities.
- **The GitHub Action exists but has not run on GitHub yet.** The checkpoint is
  intentionally uncommitted and unpushed until the user approves it.
