# Kanon Labs — v0 Technical Build Plan

> Phase-by-phase plan to build the v0 of the product described in [`WHAT-TO-BUILD.md`](./WHAT-TO-BUILD.md), on top of the Agent Tune pipeline ([`agentune.md`](./agentune.md)).
> Startup: **Kanon Labs**. Design philosophy: **reuse the boring 80%, own only the irreducible core, defer everything else.**

---

## 0. What v0 is (and isn't)

**v0 in one sentence:** feed a spec → compile a *deterministic, stateful twin* (data, not hand-written code) → run the generated intent×policy×persona scenario matrix against it → score by state-diff + policy invariants (no LLM in the pass/fail path) → surface **per-slice pass^k deltas vs. the last green run** in a dashboard and as a CI gate.

**The one design decision that shapes everything:** the twin is a **generic deterministic engine + a generated declarative "behavior pack" (data)**, *not* a code-generated app per provider. The engine is written once; per-spec output is validated data you can hand-edit (the calibration knob). This is lazier and more maintainable than codegen-a-FastAPI-app, and more deterministic than Arga's per-provider hand-written code.

### In scope for v0
- Twin compiler: OpenAPI (+ optional samples) → behavior pack → running deterministic twin with reset/snapshot.
- Policy invariants: Policies step output → Python predicate checks over `(state, trajectory)`.
- Scenario runner + deterministic scorer (fork of tau2-bench).
- Per-slice pass^k + regression diff vs baseline.
- CI gate (GitHub check / exit code) + Next.js dashboard (results grid, regression diff, scenario drill-in).
- One end-to-end worked example: `data/health-insurance/`.

### Explicitly OUT of v0 (deferred — see §11)
GEPA/DSPy auto-fix loop · MCP-agent-as-SUT beyond a basic adapter · CEL/Rego policy authoring · multi-provider twin composition · in-app full trace store (deep-link Langfuse) · browser/voice testing · SaaS multi-tenant billing · non-OpenAPI spec inputs (MCP/Python) beyond a thin adapter.

---

## 1. Architecture

```
                        ┌─────────────────────── BUILD-TIME (offline, LLM allowed) ───────────────────────┐
  spec.json  ──►  Tool Graph ──►  Guidance ──►  Policies ──►  User Stories        Twin Compiler
 (+samples)      (deterministic) (intents/    (rules +      (intent×policy×       spec+samples+policies
                                  slots)       violations)   persona matrix)      └─► BEHAVIOR PACK (data)
                        └──────────────── existing Agent Tune pipeline ──────────────┘         │
                                                                                               ▼
  ┌──────────────────────────── RUN-TIME (deterministic; NO LLM in scoring) ─────────────────────────────┐
  │                                                                                                       │
  │   Scenario Runner  ──►  Agent (SUT)  ◄──►  Twin Engine (behavior pack + in-mem store + control plane) │
  │   (tau2 orchestrator     tool calls        /__admin/{reset,snapshot,restore}                          │
  │    + LLM user sim)                                                                                    │
  │        │  k trials/scenario                                                                           │
  │        ▼                                                                                              │
  │   Scorer:  state-diff (expected/allowed/forbidden)  ×  policy invariants (Python)  ×  trajectory      │
  │        │                                                (agentevals)                                  │
  │        ▼                                                                                              │
  │   pass^k per slice  ──►  Regression diff vs last green  ──►  CI gate + Dashboard                      │
  └───────────────────────────────────────────────────────────────────────────────────────────────────┘
```

**Two-time-zone rule:** LLM is allowed at build-time (compiling the behavior pack, generating stories/invariant drafts, playing the user in simulation). LLM is **forbidden in the run-time scoring path** — state-diff and invariants are pure Python. That's the entire trust argument.

---

## 2. Reuse manifest (lift, don't build)

| Layer | Reuse | What we take | Integrate/Fork | License |
|---|---|---|---|---|
| Pipeline front | **Agent Tune** `tool_graph`, `guidance`, `policies`, `user_stories` | Already produces the matrix + policy objects | build on | in-repo |
| Twin models | **datamodel-code-generator** | OpenAPI → Pydantic v2 models for request/response validation | dep | MIT |
| Twin validation | **openapi-core** | runtime request/response conformance inside the engine | dep | BSD-3 |
| Twin conformance test | **Schemathesis** | fuzz the twin against its own spec to catch drift | dep (dev) | MIT |
| Twin MCP face | **FastMCP** `from_fastapi(app)` | expose the twin over MCP for MCP-using agents | dep | Apache-2.0 |
| Twin debug | **MCP Inspector** | interactive tool poking | tool | MIT |
| Run loop | **tau2-bench** `orchestrator/` + `runner/` | turn-based agent↔user loop, N-trial runner | **vendor 4 modules** | MIT |
| State-diff scoring | **tau2-bench** `evaluator/` | replay reference actions → target hash; `db_diff` | vendor | MIT |
| pass^k | **tau2-bench** `metrics/agent_metrics.py::pass_hat_k` | `C(success,k)/C(n,k)`, ~30 lines | vendor | MIT |
| User simulator | **tau2-bench** `user/user_simulator.py` | persona+scenario prompt template, STOP/TRANSFER tokens | vendor | MIT |
| 3-bucket state check | **AppWorld** | *pattern only*: expected/allowed/**forbidden** deltas | copy pattern | Apache-2.0 |
| Trajectory assertions | **agentevals** | `create_trajectory_match_evaluator` (unordered/subset) | dep | MIT |
| Frontend shell | **Langfuse `web/`** | app shell, TanStack Table/filter components, Recharts wrappers | copy components | MIT |
| Trace waterfall idiom | **Phoenix** `TimelineBar`/`SpanTreeItem` | layout only (deep-link Langfuse for real traces) | copy layout | Elastic-2.0¹ |
| Tool-graph viz | **react-flow (@xyflow/react)** | node-edge DAG | dep | MIT |
| TS API client | **openapi-typescript / orval** | typed client generated from FastAPI's OpenAPI | dep | MIT |

¹ Phoenix is Elastic-2.0 — copy the *visual layout pattern*, not the code, to stay clean. Langfuse (MIT) is the primary lift.

**The irreducible core we actually write:** the generic twin engine, the behavior-pack schema + LLM compiler, the policy-predicate layer, the slice-grouped scorer glue, the regression differ, and the frontend's two signature screens. Everything else is lifted.

---

## 3. Repo layout (extends Agent Tune's described structure)

```
src/agent_tune/
  tool_graph/  guidance/  policies/  user_stories/     # EXISTING pipeline — reuse as-is
  twin/                    # NEW — the deterministic twin
    engine.py             #   generic CRUD/state engine (spec + pack + store)
    store.py              #   dict-of-dicts + deepcopy snapshot/restore
    pack.py               #   BehaviorPack schema (pydantic) + loader/validator
    compiler.py           #   spec+samples+policies -> BehaviorPack (LLM offline)
    control.py            #   /__admin/{reset,snapshot,restore}
    mcp.py                #   FastMCP.from_fastapi wrapper
  gate/                    # NEW — the scoring gate (vendored tau2 lives under gate/_tau2/)
    _tau2/                #   vendored: orchestrator, runner, evaluator, metrics, user
    runner.py            #   scenario matrix -> k trials -> RunResult
    scorer.py            #   state-diff (3-bucket) × invariants × trajectory -> reward
    invariants.py        #   Policy -> Python predicate registry
    metrics.py           #   pass^k grouped by slice
    regression.py        #   this-run vs baseline -> per-slice delta
    ci.py                #   exit code + markdown summary for PR
  service/                # EXISTING FastAPI — add /twin and /gate routes
ui/                       # EXISTING Next.js — add screens (§ Phase 5)
```

---

## 4. Phase 0 — Foundations (½–1 day)

**Goal:** runnable skeleton + the example wired end-to-end-empty.

- Confirm the pipeline artifacts exist for `data/health-insurance/` (tool graph, guidance, policies, user stories). If the physical `src/` isn't present yet, Phase 0 also means standing up the pipeline modules from the Agent Tune spec — but treat that as *existing* for planning.
- Pin deps: `datamodel-code-generator`, `openapi-core`, `schemathesis`, `fastmcp`, `agentevals`. Vendor tau2 modules into `gate/_tau2/` (copy, keep LICENSE).
- **Done when:** `agent-tune twin build data/health-insurance/v2/api.json` runs and prints "TODO" without error; `pytest -q` green on an empty smoke test.

**Ponytail cut:** no new spec formats. OpenAPI only in v0; MCP/Python via a later adapter.

---

## 5. Phase 1 — Twin compiler + engine (the core, ~1 week)

**Goal:** `spec (+samples) → BehaviorPack → running deterministic stateful twin`.

### 5.1 Behavior pack schema (`twin/pack.py`)
The generated artifact. **Data, not code.** Pydantic model:
```python
class BehaviorPack(BaseModel):
    resources: dict[str, ResourceSpec]  # name -> {id_field, schema_ref, seed[]}
    transitions: dict[str, list[Transition]]  # resource -> legal state FSM edges
    invariants: list[InvariantRef]  # cross-field/-resource integrity (engine-checked)
    errors: list[ErrorRule]  # provider-shaped error envelopes + triggers
    routes: dict[str, RouteBinding]  # operationId -> {resource, verb, effects}
    hooks: dict[str, str]  # operationId -> optional python hook path (escape hatch)
    coverage: CoverageReport  # which ops are deterministic vs LLM-fallback-flagged
```

### 5.2 Generic engine (`twin/engine.py`, `store.py`, `control.py`)
Written **once**, interprets any pack:
- `store.py`: `dict[resource][id] -> record`; `snapshot()`/`restore()` = `copy.deepcopy` (the json-server model, ~100 lines).
- `engine.py`: FastAPI app; for each spec operation, dispatch by `RouteBinding` (CRUD verb + resource + effects); validate req/resp with **openapi-core**; enforce `transitions` (reject illegal state moves with the provider error) and integrity `invariants` on writes; server-set derived fields (`id`, `createdAt`).
- Deterministic seeding: spec `examples` first, else **fixed-seed Faker** (never random).
- `control.py`: `/__admin/reset|snapshot|restore` — free because we own the store.
- Optional per-op **Python hook** escape hatch for logic the declarative pack can't express (flagged in coverage).
- Truly-uncovered ops → **flagged LLM fallback** (`X-Twin-Stub`-style header + coverage log). Never silent.

### 5.3 The compiler (`twin/compiler.py`) — LLM, offline
Input: parsed spec + `api_samples.json` + Policies output. Output: a `BehaviorPack`.
- Models via **datamodel-code-generator** (never hand-write schemas).
- LLM fills the irreducible parts a spec can't contain: **legal transitions** (resource FSM), **cross-resource invariants**, **provider error catalog**, **derived-field/side-effect rules**. Policies' preconditions/postconditions seed these.
- **Validate the generated pack against reality:** replay `api_samples.json` request/response pairs through the twin; any mismatch is a compiler error to fix or flag. This is the fidelity gate.

### 5.4 MCP face (`twin/mcp.py`)
`FastMCP.from_fastapi(app)` → same twin, MCP transport, shared state. Debug with MCP Inspector.

**Done when (runnable checks):**
- `schemathesis run` against the twin passes (twin conforms to its own spec).
- Replaying `api_samples.json` reproduces recorded responses (fidelity check).
- A scripted POST→GET→PATCH→GET sequence shows state persisting and an illegal transition returning the correct provider error.
- `snapshot → mutate → restore` returns identical state.

**Ponytail cuts:** no pagination/rate-limit realism unless a policy/story needs it; no cross-twin composition; hooks only where a sample proves the declarative pack insufficient. Mark each cut in the pack's `coverage`.

---

## 6. Phase 2 — Policy invariants (~2–3 days)

**Goal:** turn the Policies step's violation conditions into deterministic checks — the Replit/Air-Canada class.

- `gate/invariants.py`: a registry of **Python predicates** `fn(state, trajectory) -> Ok | Violation(msg, turn)`. Decision (from research): Python predicates for v0, **CEL as documented upgrade path**, reject Rego/JSONLogic (ordering/quantification over the trajectory needs real code; e.g. *"no `issue_refund` without a prior `confirm` in-thread, refunded == confirmed amount"* is 5 lines of Python, awkward-to-impossible in JSONLogic).
- Two invariant kinds:
  1. **State invariants** — checked by the twin engine at write-time (integrity) *and* post-run (final state).
  2. **Trajectory invariants** — checked post-run over the message/tool-call sequence; use **agentevals** `unordered`/`subset` for the "tool X was/wasn't called" half, Python for the ordered/cross-referenced half.
- The compiler drafts predicate stubs from each policy's violation condition; a human confirms/edits (calibration knob). Each policy → happy/violation/edge stories already exist from the pipeline.

**Done when:** a hand-seeded "refund > cap" trajectory fails its invariant with the exact turn cited; a compliant one passes. One `test_invariants.py` with both cases.

---

## 7. Phase 3 — Scenario runner + scorer (fork tau2, ~1 week)

**Goal:** run the matrix against the twin, k trials each, produce deterministic rewards.

- Vendor tau2 `orchestrator/` (turn-based agent↔user loop) + `runner/` (N parallel trials). Strip voice/gym/domain baggage.
- **User simulator:** vendor tau2's `user_simulator.py` prompt template (persona + scenario + STOP/TRANSFER/OUT_OF_SCOPE). Keep persona-grounding + "reveal one issue at a time / don't do the agent's job" as an explicit **tunable knob** to fight cooperativeness bias (known failure mode; audit: arXiv 2607.02577).
- **Agent adapter (SUT):** thin interface — v0 supports (a) an OpenAI-tools agent and (b) a WxO agent (Agent Tune already integrates WxO). Tool calls route to the twin's wrapper endpoint.
- **Scorer (`gate/scorer.py`):** multiplicative gate à la tau2 `reward_basis`:
  `reward = state_ok × invariants_ok × trajectory_ok` (1.0 only if all pass).
  - `state_ok`: tau2 target-hash diff **extended with AppWorld's 3-bucket** model — expected deltas must occur, allowed may, **forbidden ⇒ fail** (catches collateral writes tau2's plain hash-equality can miss).
  - `invariants_ok`: Phase 2 predicates.
  - `trajectory_ok`: agentevals shape match where the story specifies it.
  - **No LLM judge as a hard gate.** NL-assertion (LLM) allowed only as a soft diagnostic, off by default.

**Done when:** running one policy's 3 stories × k=5 against the twin yields per-trial rewards and a readable failure diff for a deliberately-broken agent prompt.

---

## 8. Phase 4 — pass^k, regression diff, CI gate (~3–4 days)

**Goal:** the wedge — per-slice regression detection.

- `gate/metrics.py`: vendor `pass_hat_k` (`math.comb(c,k)/math.comb(n,k)`), **group by slice** = (intent, policy, persona). Emit pass@1 and pass^k per slice (the gap is the story).
- `gate/regression.py`: diff current run vs **last green run** per slice → `Δpass^k`. This is what catches "aggregate flat at 67.5%, one slice 100%→33%."
- `gate/ci.py`: threshold policy (e.g. fail if any slice Δpass^k < −X or any policy-violation slice regresses) → exit code + **markdown PR summary**. Store run artifacts + designate "green baseline."

**Done when:** intentionally regress one prompt slice; the gate exits non-zero and the markdown names the exact collapsed slice while aggregate barely moves. One `test_regression.py` asserting this.

---

## 9. Phase 5 — Frontend (~1.5 weeks; design system arrives later)

**Stack (mirror Langfuse, all restyleable):** Next.js 15 App Router (existing `ui/`), **shadcn/ui** (Radix + Tailwind + CVA + lucide), **TanStack Query** (poll long runs) + RSC for static loads, **TanStack Table v8**, **Recharts** (trends), **react-flow** (tool graph), typed client **generated from FastAPI's OpenAPI** (openapi-typescript/orval — backend is Python, so no tRPC). Read the `dataviz` skill before choosing heatmap colors.

Screens, in build priority:

1. **Regression View (killer, build first):** same heatmap geometry as overview, cells colored by **Δpass^k on a diverging scale** (red regressed / grey flat / green improved) + a **regression table sorted by worst Δ** (borrowed from Braintrust's sort-by-regression) + CI gate verdict with copy-as-markdown. **The heatmap is a plain CSS grid of divs + shadcn Tooltip — not a datagrid lib.**
2. **Results Overview:** KPI tiles (aggregate pass^k, worst slice, # slices regressed) + the **pass^k heatmap** (sequential scale, cell = k-of-n, hover shows pass@1↔pass^k gap) + a **coverage/honesty banner** (% deterministic vs LLM-fallback-flagged).
3. **Scenario Run detail:** multi-turn transcript (role-tagged, inline tool-call chips) · tool-call inspector (args→mocked response, `deterministic|stubbed` flag per call) · **twin state before/after JSON diff** · **policy-invariant checklist** (pass/fail + the turn/state that tripped it — the *why*) · trials strip for k · "Open full trace in Langfuse" deep-link.
4. **Experiments list + New-experiment launcher** (matrix scope multi-selects, `k`, baseline = last green).
5. **Artifacts:** tool graph (react-flow) · intents/policies/stories master-detail (policy detail shows pre/post/violation/edge + spawned stories).
6. **Trace:** deep-link Langfuse; in-app only a thin div-based waterfall (Phoenix layout pattern) if needed.

**Done when:** the health-insurance run renders in Overview + Regression, and a failing slice drills to the transcript + invariant checklist showing the violated turn.

**Ponytail cuts:** no in-app span store (deep-link), no custom charting lib, react-flow only when the tool-graph screen is actually built.

---

## 10. Phase 6 — Package + demo (~2–3 days)

- CLI: `agent-tune twin build|serve`, `agent-tune gate run --baseline <id>`.
- GitHub Action wrapping `gate/ci.py` (posts the markdown check — the Arga-shaped CI surface, but from *your* spec).
- One polished end-to-end demo on `data/health-insurance/`: build twin → run matrix → introduce a prompt regression → gate goes red → dashboard shows the collapsed slice.

**Done when:** `README` quickstart reproduces the demo from a clean checkout.

---

## 11. Deferred to v1 (named, so it's tracked not forgotten)

`# ponytail:` deferrals —
- **GEPA/dspy auto-fix loop** — cleanly pluggable (`GEPAAdapter.evaluate` + `make_reflective_dataset`) but optimizes the SUT/sim prompts, orthogonal to shipping the gate; wire in once v0 emits scored failing traces (its input corpus). Upgrade path: `gate/optimize.py`.
- **CEL policy authoring** — only when non-engineers author policies as data. `celpy` behind the same predicate interface.
- **Multi-provider twin composition** (unified state across twins).
- **MCP/Python spec inputs** beyond a thin adapter; pagination/rate-limit/idempotency realism in the engine.
- **Native in-app trace viewer** — only if customers can't run Langfuse.
- **Browser/voice SUTs**, SaaS multi-tenant billing.

---

## 12. Risks + calibration knobs

| Risk | Mitigation (the knob) |
|---|---|
| Spec lacks business logic (the whole hard part) | Compiler drafts from Policies + validates against `api_samples.json`; **behavior pack is hand-editable data**; Python-hook escape hatch. |
| Compiler gets an invariant/transition wrong | Fidelity gate (replay samples) + human confirm step; wrong rules are a data edit, not a redeploy. |
| Uncovered endpoints silently faked | **Flagged LLM fallback + coverage banner** — never silent (avoids the `{}`-scores-100% sin). |
| LLM user sim too cooperative | Persona-grounding + reveal-one-issue-at-a-time as an explicit tunable prompt knob. |
| Agent non-determinism | That's what pass^k measures; the *environment* is deterministic, so failures are real. |

---

## 13. Sequencing

```
Phase 0 ─► Phase 1 (twin) ─┬─► Phase 3 (runner+scorer) ─► Phase 4 (pass^k/regression/CI) ─► Phase 6
                           └─► Phase 2 (invariants) ─────┘                                    ▲
                                                          Phase 5 (frontend) ────────────────┘
```
Phases 2 and (early) 5 parallelize with 1/3. Critical path: **1 → 3 → 4**. Rough v0: ~5–6 focused weeks solo; less with the frontend design skill running alongside.

---

## Sources
Twin/mock: [datamodel-code-generator](https://github.com/koxudaxi/datamodel-code-generator) · [openapi-core](https://github.com/python-openapi/openapi-core) · [Schemathesis](https://github.com/schemathesis/schemathesis) · [FastMCP OpenAPI](https://gofastmcp.com/integrations/openapi) · [Prism (ref only)](https://github.com/stoplightio/prism) · [Mockoon CRUD (ref)](https://mockoon.com/docs/latest/api-endpoints/crud-routes/)
Harness/scoring: [tau2-bench](https://github.com/sierra-research/tau2-bench) · [tau2 evaluation.md](https://github.com/sierra-research/tau2-bench/blob/main/docs/evaluation.md) · [AppWorld](https://github.com/StonyBrookNLP/appworld) · [agentevals](https://github.com/langchain-ai/agentevals) · [GEPA](https://github.com/gepa-ai/gepa) · [CEL](https://cel.dev/) · user-sim validity [arXiv:2607.02577](https://arxiv.org/html/2607.02577v1)
Frontend: [Langfuse repo](https://github.com/langfuse/langfuse) · [Braintrust comparing-experiments](https://www.braintrust.dev/foundations/comparing-experiments) · [Phoenix tracing](https://deepwiki.com/Arize-ai/phoenix/5.1-tracing-and-observability) · [pass@k vs pass^k](https://www.philschmid.de/agents-pass-at-k-pass-power-k) · [react-flow](https://reactflow.dev/)
