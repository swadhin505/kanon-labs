# What to build in agent testing/validation

> Research synthesis + recommendation. Built on the Agent Tune repo, benchmarked against Arga Labs and the broader agent-testing space (mid-2026).
> Startup: **Kanon Labs**.

---

## TL;DR

**Build a spec-to-deterministic-twin compiler, shipped as a per-slice CI regression + policy gate.**

Take the pipeline Agent Tune already has (spec → tool graph → integrity constraints → policies) and use the LLM *offline, once* to **code-generate a deterministic stateful mock** — the same artifact Arga hand-writes per provider — then run the generated intent×policy×persona scenario matrix against it in CI, scoring by **state-diff + policy invariants, with no LLM in the pass/fail path**, and report **per-slice pass^k deltas vs. the last green run**.

This sits on the one unsolved problem where Agent Tune's assets are an unfair advantage, and it attacks the four most-corroborated pains at once.

---

## The three findings, compressed

### Agent Tune (this repo)
An *LLM-emulated* environment derived from a spec. Genuinely novel assets:
- **Tool graph** — deterministic param-flow dependency analysis, no LLM.
- **Policies step** — extracts preconditions/postconditions/**violation conditions**/edge cases, each spawning happy/violation/edge stories, stratified across intent×policy×persona.

Inherited weakness: LLM tool emulation hallucinates inconsistent state. ToolEmu measured **~31% of flagged failures as emulation artifacts, not real bugs**. Scales to any spec instantly, but you can't fully trust the environment. Built on τ²-bench + GEPA.

### Arga Labs (the opposite pole)
Hand-built, Dockerized, **deterministic** stateful twins per SaaS — explicitly *not* LLM-emulated, *not* record/replay. Faithful auth/errors/state, reset/snapshot control plane, some exposed as MCP with shared REST+MCP state, wired into a per-PR sandbox that routes unchanged services to prod.

Cost of that fidelity: **manual, ~17 twins, public SaaS only.** It will never build a twin for *your* internal APIs. Scoring/assertion model is their thinnest public area; the "auto-fix" loop is launch copy, not docs. Closed source, ~$1k/mo team tier.

### The space + the pain
One structural fact organizes everything: *every LLM-in-the-loop component — user simulator, judge, emulator — has a systematic, correlated error that ensembling masks but never removes.*
- Judge juries of 9 have **~2 effective independent votes**; only human-anchored calibration is trusted, and almost no tool enforces it.
- Simulators are too cooperative — success swings **~9 pts by which LLM plays the user**, with dialect fairness gaps.
- Emulators hallucinate state (~31%).

So the whole field is migrating toward **deterministic outcome checks** (state diff, unit tests) wherever the task permits.

Loudest, least-vendor-tainted pains:
1. **Non-determinism breaks assert-testing — and frameworks punted.** LangChain #34810 ("first-class testing framework") closed as **not planned**. Universal.
2. **Prompts regress but get ~1% of test coverage** (non-vendor arXiv:2509.19185). Validated whitespace.
3. **"The prompt you're afraid to change" + silent drift** — aggregate stays flat at 67.5% while one slice collapses 100%→33%; caught weeks later by a support ticket.
4. **Silent tool-call failures** (200 OK, semantically wrong) compound invisibly.
5. Most teams ship on **vibes** — a JSON of hand-written cases + a run-once vibe check. No standard stack.
6. The verified production disasters — **Replit deleting a DB despite ALL-CAPS prohibition, Air Canada inventing a refund policy** — are **policy-enforcement** failures, not capability failures.

---

## The insight

Agent Tune and Arga are the two ends of **the one unsolved dilemma** (emulation fidelity, a.k.a. replay-vs-realism): *LLM-emulated scales but hallucinates; hand-built is faithful but doesn't generalize.* **Nobody occupies the middle.**

The middle exists, and Agent Tune is uniquely positioned to take it, because **a deterministic twin is mostly a state machine over a schema plus integrity constraints — and Agent Tune already extracts exactly that.** The move: **demote the LLM from runtime responder to compile-time code generator.** Generate the twin's code and invariants once, offline, validate against provided `api_samples.json` / prod-snapshot — then at test time the environment is pure deterministic code.

You get Arga's fidelity *and* Agent Tune's "works from any spec, including proprietary internal tools Arga will never touch."

This reframes Agent Tune from "a better τ-bench generator with a shaky emulator" (competing with a research field) into **"the thing that compiles your spec into the deterministic twin + regression gate Arga sells for $1k/mo — but for *your* APIs, not just the 17 common SaaS."**

---

## What to build, concretely

### 1. Spec → deterministic twin compiler
LLM offline turns spec + tool graph + samples into a stateful in-memory mock: CRUD + state transitions + provider-shaped errors, with a reset/snapshot control plane. Validate generated behavior against `api_samples.json`.

Business logic the spec doesn't contain comes from the Policies step (invariants).

**Hybrid honesty:** the ~20% long tail the compiler can't nail falls back to LLM emulation — *flagged and logged* (Arga's `X-Twin-Stub` pattern) so coverage is never silently overstated (the most common eval sin: a `{}` scoring 100%).

### 2. Deterministic scoring — three checkable layers, zero LLM judge in the pass/fail path
- **State diff** — final twin state vs. the story's expected state (τ-bench's proven approach).
- **Policy invariants** — the violation conditions Agent Tune already extracts, compiled into runtime assertions against twin state + trajectory ("never refund > cap", "never delete without confirmation"). The Replit/Air Canada class — deterministically checkable *because the twin owns its state*. **Nobody else auto-derives these from the spec.**
- **pass^k per slice** — run each scenario k times; report worst-case consistency, not best-case.

### 3. The regression gate — this is the wedge, not a feature
Run the intent×policy×persona matrix before/after any prompt/model change; surface the **per-slice pass^k delta**. The stratified matrix is precisely what catches "aggregate flat at 67.5%, one slice collapsed to 33%" — today caught only by a customer complaint weeks later. It's a CI check: where the pain is felt, and where the money already flows.

### Where the LLM stays (essential, but out of the trust-critical path)
- Offline twin/invariant codegen.
- Multi-turn user simulation — but τ²-style *tool-constrained*, persona-grounded in real traces, to fight the cooperativeness/dialect bias.
- *Optional* semantic judge for open-ended dialogue quality only, human-calibrated, never gating correctness.

---

## What NOT to build

- **Not another trace dashboard.** Langfuse/Braintrust/Arize own it; Agent Tune already uses Langfuse. Commodity.
- **Not hand-built twins for common SaaS.** Arga is ahead and it's a grind — *compile from spec* to complement, don't race them on the 17.
- **Not another LLM-judge.** A dozen exist and practitioners distrust all of them.
- **Not browser E2E.** Arga/Stagehand own intent-based browser testing.

---

## The honest risk + de-risking

The hard part is what Arga hand-writes: the **business logic a spec doesn't contain** ("refund ≤ original charge"; SOQL semantics). A schema gives CRUD shape, not domain rules. The bet: the Policies step + provided samples/prod-seed extract enough offline, and the honest hybrid (deterministic 80%, flagged-LLM fallback for the tail) plus **a human calibration knob for the rules the LLM gets wrong** covers the rest. Leave the knob — the domain has invariants a minimal generated model can't see.

Fallback if spec+samples can't reach useful twin fidelity: Agent Tune's spec-derived **policy invariants + stratified regression matrix** are valuable *even layered on top of Arga's twins or a hand-mocked env*. That's the defensible core regardless.

---

## Sharpest version

Everyone is building better ways to *judge* agents; the trustworthy signal is a *deterministic environment that owns its own state*. Agent Tune is one refactor — LLM from runtime to compile-time — away from being the only tool that generates that environment, and its policy invariants, straight from your spec.

---

## Naming

Startup name: **Kanon Labs**.

---

## Code quality & structure (non-negotiable)

The whole product rests on one promise — *this signal is trustworthy*. Sloppy code quietly breaks that promise: a mis-scoped store, a leaky snapshot, a silently-swallowed error, and the "deterministic" oracle lies. So code quality here is not hygiene, it's the product.

**Clean code — very important:**
- **Determinism is a code property, not a hope.** No hidden global state, no wall-clock/`random` in the run-time path, pure functions in the scoring path. If it can't be reasoned about, it can't be trusted.
- **Small, single-responsibility units** with typed boundaries (Pydantic models, typed signatures). The scoring path especially stays pure and readable — someone must be able to audit *why* a run passed at 3am.
- **Errors surface, never swallow.** A twin fallback or an uncovered endpoint is *logged and flagged*, never silent — the same honesty rule that keeps the eval valid.
- **One runnable check per non-trivial unit** (assert-based self-check or a small `test_*.py`). Lazy code without its check is unfinished.
- Boring over clever. Reuse the boring 80% (see build plan); own only the irreducible core, and keep that core legible.

**Clean folder structure — important:** the repo layout must make the two-time-zone rule (build-time LLM / run-time deterministic) visible at a glance — the twin, the gate, and the scoring path each in their own clearly-named module, so the trust boundary is obvious from the tree, not buried. The concrete layout lives in the [v0 build plan](./V0-BUILD-PLAN.md).

---

## Sources

**Arga:** [argalabs.com](https://www.argalabs.com/) · [docs](https://docs.argalabs.com/) · [digital-twins](https://docs.argalabs.com/concepts/digital-twins.md) · [twin-reference](https://docs.argalabs.com/concepts/twin-reference.md) · [session-replay](https://docs.argalabs.com/concepts/session-replay.md) · [sandbox-config](https://docs.argalabs.com/features/sandbox-config.md) · [GitHub org](https://github.com/ArgaLabs) · [twin-take-home](https://github.com/ArgaLabs/twin-take-home) · [YC launch](https://www.ycombinator.com/launches/PwC-arga-labs-real-world-sandboxes-for-multi-app-agents-and-software)

**Benchmarks/architecture:** τ-bench [arXiv:2406.12045](https://arxiv.org/abs/2406.12045) · τ²-bench [arXiv:2506.07982](https://arxiv.org/abs/2506.07982) · WebArena [arXiv:2307.13854](https://arxiv.org/abs/2307.13854) · AppWorld [arXiv:2407.18901](https://arxiv.org/abs/2407.18901) · SWE-bench [arXiv:2310.06770](https://arxiv.org/abs/2310.06770) · BFCL [leaderboard](https://gorilla.cs.berkeley.edu/leaderboard.html) · ToolEmu [arXiv:2309.15817](https://arxiv.org/abs/2309.15817) · GEPA [arXiv:2507.19457](https://arxiv.org/abs/2507.19457)

**Eval methodology:** LLM-judge bias [arXiv:2406.07791](https://arxiv.org/abs/2406.07791) · panels/PoLL [arXiv:2404.18796](https://arxiv.org/abs/2404.18796) · correlated-judge-error [arXiv:2605.29800](https://arxiv.org/abs/2605.29800)

**Market pain:** LangChain #34810 [closed not-planned](https://github.com/langchain-ai/langchain/issues/34810) · prompts ~1% coverage [arXiv:2509.19185](https://arxiv.org/abs/2509.19185) · prompt drift [densitylabs](https://densitylabs.io/blog/regression-set-for-prompt-changes/) · silent tool failures [arize](https://arize.com/blog/common-ai-agent-failures/) · benchmark hacking [learnagentic](https://learnagentic.substack.com/p/every-major-agent-benchmark-just) · Replit incident [theregister](https://www.theregister.com/2025/07/21/replit_saastr_vibe_coding_incident/) · model-upgrade safety drift [promptfoo](https://www.promptfoo.dev/blog/model-upgrades-break-agent-safety/)

**Industry tools:** [Coval](https://www.coval.ai/blog) · [DeepEval](https://deepeval.com) · [Patronus](https://www.patronus.ai/products) · [Okareo](https://docs.okareo.com/docs/terminology) · [Braintrust](https://www.braintrust.dev/blog/brainstore-architecture) · [Galileo Luna-2](https://galileo.ai/blog/introducing-luna-2-purpose-built-models-for-reliable-ai-evaluations-guardrailing) · [Hamel evals-faq](https://hamel.dev/blog/posts/evals-faq/) · sandboxes: [E2B](https://memo.d.foundation/breakdown/e2b) / [Modal](https://modal.com/resources/best-code-execution-sandboxes-ai-agents) / [Daytona](https://www.daytona.io/docs/llms-full.txt)
