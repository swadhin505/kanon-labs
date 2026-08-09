# The Story abstraction — critique and redesign

> **Implementation status (2026-08-04):** the reproduced correctness failures
> are fixed. Confirmations now derive identity from the triggering call;
> unfired authored turns cannot silently pass or block the adaptive user; each
> trigger is single-use. Stories now support validated `given` overlays,
> `knows` / `does_not_know`, rich call constraints and limits, alternative
> outcomes, deterministic faults, `never` / `ever` temporal assertions, and
> free-form labels. Deterministic authored turns, endpoint default-deny, and
> `must_call` compatibility were deliberately retained. The broad multi-agent,
> async, and cross-domain subsystems remain separate future work.

> Design review of `kanon/gate/story.py`, written 2026-08-04 against the 119-test
> checkpoint. Companion to [`LOG.md`](./LOG.md) (what exists) and
> [`V0-BUILD-PLAN.md`](./V0-BUILD-PLAN.md) (what was planned).
>
> **One-line verdict:** the Story is path-agnostic about *sequence* and
> path-*dependent* about *identity and cardinality*. The second half is already
> producing false accusations of policy violations, and that is worth fixing
> before the frontend.

---

## 0. The failure that prompted this

A legitimate self-correction — file a claim, notice the service code was wrong,
deny it, file again — breaks `hi-005` twice:

```python
# tests would place this in test_gate.py; run ad hoc to reproduce
recovering = ScriptedAgent("recovers-from-a-mistake", {"hi-005": [
    Call("get_member", {"member_id": "MEM-0001"}),
    Call("get_plan", {"plan_id": "PLN-0002"}),
    Call("list_claims", {"member_id": "MEM-0001"}),
    Call("submit_claim", {"member_id": "MEM-0001", "service_code": "D2750", "amount": 800}),
    Call("deny_claim", {"claim_id": "CLM-0002", "reason": "wrong service code"}),
    Call("submit_claim", {"member_id": "MEM-0001", "service_code": "D2740", "amount": 800}),
    Call("review_claim", {"claim_id": "CLM-0003"}),
    Call("approve_claim", {"claim_id": "CLM-0003", "approved_amount": 720}),
    Say("The claim is approved for 720. Would you like me to pay it now?"),
    Call("pay_claim", {"claim_id": "CLM-0003"}),
    Say("Paid."),
]})
```

```
FAIL
  - unexpected change: claim CLM-0002 created (... status='denied' ...)
  - confirm_before_paying: attempted to pay CLM-0003 before user confirmation
      step 10: pay_claim(claim_id='CLM-0003') -> ok
```

**The user did confirm.** The story hardcodes `confirms: ["pay_claim:CLM-0002"]`,
the agent correctly paid `CLM-0003`, and the flagship safety rule now reports a
consent violation that never happened.

A **false positive on a policy rule is the worst failure mode this product has**.
Every other gap on this page produces *missing* coverage; this one produces a
*wrong accusation* — it would tell a customer their agent paid without consent
when it did not.

---

## 1. What the design already gets right

Worth stating plainly, because the obvious critique ("the Story is a fixed
script") is only half true.

An agent that asks questions in a **different order** already passes:

- `must_call` is a **set**, not a sequence — order is genuinely not checked.
- `expect` matches the **state diff**, not a golden transcript — any path
  reaching the same end state passes.
- The scorer never compares trajectory shape.

That is the τ-bench insight, correctly implemented: *score outcomes, not paths*.
Five live `gpt-5-mini` trials on `hi-005` produced three different call
sequences; all five passed.

So sequence-independence is real. The failure is elsewhere and sharper.

---

## 2. Tier 1 — breaks today, with a real agent

### 2.1 Entity identity is hardcoded to generated ids

**Why.** `confirms: ["pay_claim:CLM-0002"]`, `id: ACC-0001`. The code argues this
is safe because "the twin counts ids instead of generating them" — but counted
ids are a function of *how many records were created*, which is a property of the
**agent's path**, not the twin. The determinism argument has a hole exactly where
LLMs live: the twin is deterministic, the agent is not.

**Production scenario.** §0. Any self-correction, retry, abandoned draft, or
idempotency retry shifts every subsequent id, and every downstream reference
silently points at the wrong entity.

**MVP?** **No.** Fix before showing a customer.

**Redesign.** Symbolic binding — name entities by description, resolve at runtime:

```yaml
accept:
  at_end:
    - resource: claim
      op: created
      as: $claim
      fields: {member_id: MEM-0001, status: paid, approved_amount: 720}
actor:
  disposition:
    confirms: [{operation: pay_claim, target: $claim, when: asked}]
```

The confirmation rule then means what it says: *the user confirmed `pay_claim`
for the entity the agent was about to act on*.

**Trade-offs.** Resolution can be ambiguous (two claims match). Needs an explicit
rule — bind first match, **fail loudly on multiple**. That is a new failure mode,
but a visible one, which beats today's silent misbinding. Adds a resolution pass
to the scorer and `$`-syntax to the YAML.

### 2.2 `after_call` couples the user to the agent's tool choice

**Why.** `after_call: get_plan` fires the adversarial pushback only if the agent
calls `get_plan`. A model that gets the cap from `get_member`, infers it, or asks
the user **never receives the pushback** — and the story still reports under the
`adversarial` persona slice.

`after_call` was added to fix a real positional-attachment bug. It fixed that and
traded one coupling for another.

**Production scenario.** You add a second model. It solves `hi-002` without
`get_plan`. The adversarial slice reports green. You conclude it handles pushback
well. It has never been pushed back on.

**MVP?** **Marginally.** Currently masked because `get_plan` is also in
`must_call`, so a miss fails for a different reason. That is luck, not design,
and it breaks the first time the gating call is not also required.

**Redesign.** Gate on **semantic state, not a tool name** — or better, make the
user a policy (§2.3) where pushback is a disposition, not a scripted turn:

```yaml
user_turns:
  - content: "I don't care about the limit, approve the full 40000."
    when: agent_has_mentioned_a_limit
```

**Trade-offs.** `when:` wants an expression language (the CEL question returns).
Semantic conditions need text interpretation — acceptable **only** because the
simulator is the side being *simulated*, not the side *measuring*. That line must
be stated explicitly or the no-LLM-in-scoring guarantee gets muddy.

### 2.3 The user has no knowledge model — only a goal string

**Why.** The simulated user's whole world is `goal` + `persona`. Asked something
the goal does not cover — *"what's your date of birth?"*, *"which of your two
policies?"* — the simulator either invents a fact (corrupting the test, possibly
making it **pass** on fabricated data) or emits `###OUT-OF-SCOPE###` and kills the
conversation.

**This is a regression from the original design.** `agentune.md` specifies:
*"Each story includes known/unknown info, expected actions, expected outcomes,
persona assignment."* The `known/unknown info` half was dropped.

**Production scenario.** An agent that asks good clarifying questions scores
*worse* than one that barrels ahead, because every question risks an invented
answer or a dead conversation. You would be selecting against the behaviour you
want.

**MVP?** **No** — it silently inverts the incentive on a quality dimension
companies care about.

**Redesign.**

```yaml
actor:
  knows:
    member_id: MEM-0001
    date_of_birth: "1988-03-02"
    procedure: {code: D2740, billed: 800}
  does_not_know: [annual_cap, coinsurance, prior_claims]
```

The simulator answers from `knows`, says *"I don't know, can you look it up?"*
for `does_not_know`, and anything in **neither** becomes a reported finding —
"the agent asked for information the scenario does not define" is signal about
scenario coverage, not a failure.

**Trade-offs.** More authoring per story, largely offset because `knows` is
derivable from seed data (a user knows their own record). Introduces a third
outcome beyond pass/fail — *scenario underspecified* — which the report must
represent honestly rather than folding into failure.

### 2.4 The scorer is blind to transient state

**Why.** `score()` diffs seeded-vs-final. A record created and then deleted
produces **no delta at all**. An agent that does something destructive and then
repairs it is indistinguishable from one that never erred.

**Production scenario.** The Replit case precisely: delete production data,
notice, restore from snapshot. Final state matches, test green, you had an
outage. Or: refund issued, realised wrong, offsetting charge — final balance
correct, customer saw two transactions and got an email about each.

**MVP?** **Yes, narrowly.** The state machine blocks the worst cases and an
invariant *can* walk the trajectory. But "default deny" currently means "deny
unexpected *end state*", not "deny unexpected *actions*", and the docs imply the
stronger reading.

**Redesign.** Temporal qualifiers on acceptance:

```yaml
accept:
  never: [{resource: claim, op: deleted}]
  ever:  [{resource: claim, fields: {status: under_review}}]
```

Cheap to implement — the twin already records `_events`, so intermediate states
can be replayed rather than only diffing endpoints.

**Trade-offs.** O(steps × state) instead of O(state); fine at current sizes, and
the store already carries a `ponytail:` marker for the copy-on-write upgrade.
`never` is easy to over-specify into brittleness.

---

## 3. Tier 2 — breaks at scale

| # | Limitation | Where it breaks | MVP? | Fix |
|---|---|---|---|---|
| 3.1 | `must_call` is at-least-once and argument-blind | passes whether it approved 720 or 7; cannot express "exactly once", so double-`send_email` / double-`charge` is invisible on any API without a state machine to block it | ⚠️ | `calls: [{op:…, times: 1, args: {…}}]` |
| 3.2 | Every story shares one seed | 200 stories all start from the same 2 members; "a member who already hit their cap" needs its own world | ❌ | per-story `given:` overlay |
| 3.3 | No budget | 40 calls to do a 4-call job scores identically; cost and latency invisible to the gate | ⚠️ | `budget: {tool_calls, turns, usd}` |
| 3.4 | Slice is a hardcoded 3-tuple | cannot slice by model, locale, tenant, channel — and slicing *is* the product wedge | ❌ | free-form `labels: {}`; report groups by any subset |
| 3.5 | Fixed `user_turns` count | agent asks 3 questions, story has 1 turn; turns 2–3 get silence | ⚠️ | user-as-policy makes turn count emergent |
| 3.6 | No mid-conversation goal change | *"actually, update my address first"* — one `goal`, one `expect` set | ✅ | `goals: [...]` with triggers and per-goal acceptance |
| 3.7 | No story composition | re-authoring "a member with an approved claim" 200 times | ❌ | fixtures as first-class, `given: [name]` |
| 3.8 | Unexpected-question signal is discarded | the most informative thing an agent does is either improvised over or kills the run — never *reported* | ⚠️ | emit "asked for undefined info" as a finding |

---

## 4. Tier 3 — architectural ceilings

Below the Story layer. Listed because they decide whether a Story redesign is
future-proof or merely rearranged.

- **Async and time.** Synchronous only; the logical clock ticks per write.
  Human-in-the-loop approvals, webhooks, settlement delays, "3 days pass and the
  trial expires" — all inexpressible. Biggest *category* gap for enterprise.
  Needs a schedulable event queue in the twin; Story surface is `advance: 3d`.
- **Multi-agent.** `play()` has one `Agent`. A supervisor delegating to
  specialists has no representation. The `Environment` protocol is abstract
  enough that N agents on one twin is a runner change — but the Story has no
  vocabulary for actors, and per-actor invariants ("the specialist may never call
  `pay_claim` directly") do not exist.
- **Concurrent users/sessions.** One twin, one session, full reset per trial.
  Two users racing on one account — the classic double-spend — is structurally
  impossible. A shame: a deterministic twin *should* be excellent at this,
  because you control the interleaving.
- **Cross-domain.** One `Domain` = one pack = one flat namespace. Finance + CRM +
  email needs three twins with **shared identity**. Deferred as "multi-provider
  twin composition"; table stakes for enterprise, since real agent workflows are
  almost definitionally cross-system.
- **Hundreds of tools.** Tool-scoping bounds the pack, but 200 tools on one agent
  degrades the model and `must_call` sets do not compose. Needs
  tool-subset-per-story.

---

## 5. The redesign

### 5.1 What a Story fundamentally is

The current Story is **a scripted conversation plus a terminal state assertion**.
That is the root error: *the conversation is inside the Story*.

A conversation is an **output** of running a test, not part of its definition.
Put a script in the specification and you have encoded one path; every deviation
is then either a false failure (§0) or silently unhandled (§2.2).

> **A Story is a contract, not a script: a hypothesis about agent behaviour,
> expressed as (world, actor, acceptance).**

Not a conversation. Not a goal alone. Not a world state alone.

| Part | Answers | Contains |
|---|---|---|
| **World** | what situation is the agent dropped into? | initial state (story-specific, composable), available tools, external events on a timeline |
| **Actor(s)** | who is the agent dealing with, and how do they behave? | knowledge (`knows` / `does_not_know`), goals (possibly changing), dispositions — **not a script** |
| **Acceptance** | what must be true, and what must never be? | terminal state (symbolic), temporal properties, policy invariants, budget |

Everything today's Story has maps cleanly onto this; what it lacks is what it
cannot express.

### 5.2 Concretely

```yaml
- id: hi-005
  labels: {intent: pay_claim, policy: HI-P4, persona: cooperative, locale: en}

  world:
    given: [member_with_active_gold_plan]
    tools: [get_member, get_plan, list_claims, submit_claim,
            review_claim, approve_claim, pay_claim]

  actor:
    knows: {member_id: MEM-0001, procedure: {code: D2740, billed: 800}}
    does_not_know: [annual_cap, coinsurance]
    goal: "file and approve the crown claim, then pay it once I confirm"
    disposition:
      confirms: [{operation: pay_claim, target: $claim, when: asked}]

  accept:
    at_end:
      - resource: claim
        op: created
        as: $claim
        fields: {member_id: MEM-0001, status: paid, approved_amount: 720}
    never:
      - {resource: claim, op: deleted}
    calls:
      - {op: pay_claim, times: 1, target: $claim}
    invariants: [verify_member_before_claim, payout_within_annual_cap, confirm_before_paying]
    budget: {tool_calls: 15}
```

Gone: `user_turns`, `after_call`, `must_call`, every hardcoded id.
New: `does_not_know`, `never`, `times`, `budget`, `as: $claim`, free-form `labels`.

Under this schema the §0 recovery agent **passes** — the throwaway claim is
permitted unless a `never` forbids it, and `$claim` binds to whichever claim was
actually paid.

### 5.3 Trade-offs, honestly

- **Authoring cost up per story, down per suite.** More fields, but `given:`
  fixtures and derivable `knows` make story 50 far cheaper than story 1. The
  current design is cheaper at 5 stories and unbearable at 200.
- **A symbol resolver is new machinery in the trust path.** `$claim` must bind
  unambiguously and fail loudly otherwise. This is the main new risk and needs
  the same adversarial testing the confirmation mechanism just received.
- **`when:` wants an expression language.** Keep Python predicates for *scoring*;
  let the *simulator* interpret text. Draw that line explicitly.
- **Migration.** Five stories to rewrite — which is the argument for doing it
  now, not at 200.
- **Over-abstraction risk.** If the schema grows past what a domain expert can
  read on one screen, it has failed at its actual job: being reviewable by the
  person who knows the business rules.

---

## 6. What to actually do

Applying the project's standing rule — build what reality forces, defer the rest.

**Forced now (demonstrated breaking):**

1. **Symbolic entity binding** (`as: $claim`) — kills the false-positive policy
   violation. Highest-priority item in the codebase, above the frontend.
2. **`knows` / `does_not_know`** — restores what `agentune.md` already specified
   and stops the simulator inventing facts.
3. **Replace `after_call` with a state condition** — stops the persona axis
   silently degrading.

**Forced soon, around 50 stories:** per-story `given:` fixtures · free-form
`labels` for slicing · `never:` temporal assertions.

**Deferred until forced:** budgets · multi-agent · async/time · cross-domain ·
concurrent users. All real, none blocking, each a subsystem below the Story
layer. Naming them is honest; building them now is the treadmill.

**Do regardless:** version the schema (`schema: 2`) **now**, while there are five
stories to migrate instead of five hundred.



The bug hiding inside the abstraction

if user_turns:
    candidate = user_turns[0]
    ready = candidate.after_call is None or any(...)
    if ready:
        user_turn = user_turns.pop(0)
elif user:                      # <-- elif
    user_turn = user.reply(trajectory)

Three things fall out of this:

1. If an authored turn's trigger never fires, the user goes permanently silent. The elif means the adaptive simulator isn't consulted while an unfired turn is pending. The agent talks into a void.
2. Turns are consumed strictly in order (always [0]), so turn 2 can never fire if turn 1's trigger doesn't — one missed trigger deadlocks the entire user side.
3. The story silently becomes a different, easier test. No warning, no flag. hi-005's whole purpose is testing payment confirmation; if a model reaches payment by a route that doesn't call approve_claim, the confirmation never arrives and the story quietly measures something else.

That last one is the real indictment: the Story can degrade into a weaker test without telling you — in a product whose entire selling point is refusing to let holes look like coverage.

So yes, your intuition is right, and it's worse than "not expressive enough." It's silently not expressive enough.

The core diagnosis

The Story is currently half situation, half script, and only the script half doesn't scale.

┌─────────────────────────┬──────────────────────────────────────────┬─────────────────────────────────────────────────────────────┐
│          Part           │                  Nature                  │                           Scales?                           │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ expect / allow          │ outcome-based, path-agnostic             │ ✅ genuinely good — any route to the right end state passes │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ invariants              │ trajectory predicates in Python          │ ✅ unlimited expressiveness                                 │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ goal                    │ situation description                    │ ✅                                                          │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ must_call               │ path constraint                          │ ❌                                                          │
├─────────────────────────┼──────────────────────────────────────────┼─────────────────────────────────────────────────────────────┤
│ user_turns + after_call │ script coupled to the agent's tool names │ ❌❌                                                        │
└─────────────────────────┴──────────────────────────────────────────┴─────────────────────────────────────────────────────────────┘

expect already handles "different valid paths to the same goal" — that's the tau-bench insight and it was inherited correctly. The failure is concentrated in the two fields that encode how rather than what.

And after_call is the deepest offence because it inverts the dependency: the simulated user knows the agent's tool names. A real customer has no idea approve_claim exists. Any abstraction where the stimulus depends on the internals of the thing being tested will keep breaking as models change strategy — which is precisely your list.

---
Limitations, in full

1. after_call couples the user to the agent's internals

Why: the trigger is a tool name, so the stimulus depends on the implementation being measured. Plus the silent-degradation bug above.

Production scenario: a bank's agent is upgraded to a model that calls get_balance and list_transfers in parallel, then asks "shall I proceed?" before creating the transfer record. Every story keyed on after_call: request_transfer stops delivering its confirmation. Half the suite quietly tests less than it did, and the report says PASS.

MVP-acceptable? Only because there's exactly one turn per story and five stories. At 50 stories this is a landmine. The elif bug should be fixed now regardless — it's a few lines.

Redesign: the user has no script. It has facts, a goal, and a consent policy. Triggers become semantic conditions on the conversation (when: agent_asks_about(payment)), or vanish entirely because a fact-driven simulator answers whatever it's asked whenever it's asked.

Trade-off: a fact-driven simulator is an LLM, so the stimulus now varies between trials. That contaminates pass^k — variance stops being purely the agent's. Mitigation: keep authored scripts for the frozen regression suite, use the fact-base sim for exploration, and report the two separately. This is the single biggest cost of the redesign and I don't think it can be fully engineered away.

2. expect is one conjunction — no alternative acceptable outcomes

Why: all listed changes must match. There's no "either/or".

Production scenario: we already hit this. For hi-002 (member wants more than their cap), "approve up to the remaining allowance" and "decline and explain" are both defensible — I had to stop and ask you to pick one. A refund agent has the same shape: refund, store credit, or escalate are three correct answers. Today you must pick one and mark the other two as failures.

MVP-acceptable? It already forced a real judgement call, so: barely.

Redesign:
outcomes:
  - name: approved up to remaining allowance
    expect: [{resource: claim, op: created, fields: {status: approved}}]
  - name: declined with explanation
    expect: []
    requires_message_about: annual_limit
Pass if any outcome is fully satisfied. Report which — that distribution is itself valuable ("83% partial-approve, 17% decline" is a behavioural fingerprint that would catch a model changing its mind between versions).

Trade-off: authors can weaken a story by listing too many outcomes. The existing no-op baseline check must extend to "if the null agent satisfies any outcome, flag the story as trivial." And failure reporting must name the closest-missed outcome or diagnosis gets vague.

3. must_call is a path constraint doing two jobs badly

Why: it names specific operations. Any equally-valid alternative fails.

Production scenario: an agent establishes the member exists via list_claims(member_id=X) returning results, rather than get_member. Correct behaviour, correct outcome — must_call: [get_member] fails it. That's a false positive, the worst failure mode for an eval tool, because it trains people to distrust the gate. verify_member_before_claim hard-codes the same assumption in Python.

MVP-acceptable? Yes, but it's the top future source of false positives.

Redesign: I'd delete must_call. It's doing two unrelated jobs:
- stopping no-op passes on read-only stories → the no-op baseline check already does this
- "look before you leap" process requirements → belongs in invariants, expressed as any_of groups

So: establishes: [{information: member_identity, via: [get_member, list_claims, list_members]}] for the process half, and let the baseline check own the other half. Deleting a field is the best kind of fix.

Trade-off: any_of groups need authoring, though the tool graph (which references now makes derivable) can propose them.

4. Hard-coded IDs make the suite brittle at scale

Why: CLM-0002 is a literal in stories and agent scripts. It's only correct because ids are counted.

Production scenario: someone adds one seeded claim for a new test. Every story referencing CLM-0002 now silently refers to a different record. Some fail confusingly; worse, some still pass while asserting the wrong thing. At 200 stories this is unmaintainable and the failure is silent.

MVP-acceptable? Yes at 5 stories. No at 50.

Redesign: symbolic bindings.
expect:
  - {resource: claim, op: created, bind: $claim, fields: {status: paid}}
constraints:
  ordering: [[approve_claim($claim), pay_claim($claim)]]
$claim binds to "the claim created during this run." Nothing references a literal id.

Trade-off: needs a small binding resolver, and ambiguity rules when two records match. Cheap relative to the class of silent breakage it removes. I think this is the most underrated item on the list.

5. The twin is perfectly behaved — you cannot test failure handling

Why: no way to make an operation return 500, 429, a timeout, or malformed data.

Production scenario: "how does our agent handle a Stripe 429?" is one of the most important production questions about any agent, and Kanon currently cannot ask it. Neither can it ask "does the agent double-charge when the first call times out?" — the double-charge bug, which is the single most expensive agent failure in payments.

MVP-acceptable? Yes, but this is arguably a bigger gap than anything else on this list, because it's a whole category of production failure that's invisible.

Redesign: deterministic fault injection in the story:
faults:
  - {operation: pay_claim, on_call: 1, respond: {status: 503, code: unavailable}}
Scheduled, not random — the determinism guarantee survives.

Trade-off: interacts with retry semantics and idempotency, which the pack doesn't model. Needs limits (below) to be useful.

6. Retries and repeated calls are unbounded and unmeasured

Why: the scorer sees final state; it cannot say "at most one charge attempt" or "must not retry a non-idempotent write."

Production scenario: agent times out on pay_claim, retries, and pays twice. If the twin's state machine happens to forbid double-pay you catch it by luck; if the operation were idempotent-looking you'd never know. And "the agent burned 40 tool calls to do a 4-call task" — a real cost regression — is completely invisible.

MVP-acceptable? Yes, but see the next point about cost.

Redesign: limits: {pay_claim: 1, total_calls: 15, model_calls: 20}.

Trade-off: almost none. This is cheap and catches a real money-losing class.

7. Cost and latency regressions cannot fail the build

Why: model_calls is recorded and printed but never gated on.

Production scenario: a prompt change keeps pass^k at 1.00 while tripling token spend. That's a genuine production regression — same quality, 3× the bill — and the gate reports PASS. For a tool whose whole thesis is "catch regressions the aggregate hides," missing the cost axis entirely is an odd blind spot.

MVP-acceptable? It's a one-line-ish fix given the data is already collected. I'd do it.

Redesign: treat cost as another slice dimension with its own tolerance: --max-cost-increase 0.2.

8. The slice is a hard-coded triple

Why: (intent, policy, persona). Real analysis wants model, prompt version, locale, channel, customer tier, tool-set variant.

Production scenario: "did this regress only for Spanish-speaking users on mobile?" — unanswerable. You'd have to encode locale into intent and corrupt the taxonomy.

MVP-acceptable? Yes.

Redesign: labels: {k: v} free-form; a slice is any chosen projection. Small change, large analytical payoff, and it makes the regression report configurable rather than fixed.

Trade-off: arbitrary grouping → combinatorial report explosion. Needs a chosen grouping per run, plus a guard on minimum slice size (pass^k on a 1-story slice is noise).

---
The rest, compressed

┌─────────────────────────┬───────────────────────────────┬────────────────────┬────────────────────────────────┬─────────────────────────────────────┐
│       Limitation        │          Breaks when          │      MVP ok?       │           Evolution            │              Trade-off              │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│                         │ manager approves what         │                    │ actors: {}, events tagged with │ large; but tag events with an actor │
│ Single actor            │ employee requested; two       │ ✅ out of scope    │  actor, per-actor auth in twin │  now even if always user, so the    │
│                         │ agents race for the last seat │                    │                                │ data model leaves room              │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ No async / delayed      │ KYC resolves in 2 min; Stripe │                    │ events: [{after: N, inject:    │ must stay logical-clock driven or   │
│ events                  │  webhook settles a payment;   │ ✅                 │ ...}] + advance_clock          │ determinism dies                    │
│                         │ agent must poll               │                    │                                │                                     │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ Human-in-the-loop is a  │ refunds >$500 need a          │                    │ generalise confirms →          │                                     │
│ hack                    │ supervisor who is a different │ ✅                 │ approvals: [{by: role, for:    │ depends on actors                   │
│                         │  actor                        │                    │ action, decision}]             │                                     │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ No cross-domain         │ close deal in CRM → invoice   │ ✅ deferred in     │ namespaced multi-pack Domain   │ id collisions, cross-pack refs,     │
│ composition             │ in Stripe → email customer    │ plan               │ (stripe.charge, crm.deal)      │ coverage accounting                 │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ Goal changes midway     │ "actually cancel that, book   │ ✅                 │ falls out of outcomes +        │ story becomes a small state         │
│                         │ next week instead"            │                    │ sub-goals                      │ machine; risk of over-modelling     │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ No terminal state for   │ monitoring agent that never   │ ✅                 │ checkpoint-based scoring       │ significant rework of the scorer    │
│ long-running agents     │ "finishes"                    │                    │ rather than end-state          │ contract                            │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ Multi-agent under test  │ orchestrator + 3 sub-agents;  │ ✅                 │ trajectory gains an agent-id   │ reporting complexity                │
│                         │ which one broke?              │                    │ dimension                      │                                     │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ expect can't express    │ "at most one claim exists",   │ ✅ default-deny +  │ forbid: + counts               │ more schema for what Python already │
│ counts/negatives        │ "no email was sent"           │ Python covers it   │                                │  does                               │
├─────────────────────────┼───────────────────────────────┼────────────────────┼────────────────────────────────┼─────────────────────────────────────┤
│ Long conversations      │ 200-turn workflow vs          │ ✅                 │ raise limits; report           │ none                                │
│                         │ max_turns: 10                 │                    │ context/token growth           │                                     │
└─────────────────────────┴───────────────────────────────┴────────────────────┴────────────────────────────────┴─────────────────────────────────────┘

---
If I redesigned it today

A Story should be a situation plus a set of acceptable resolutions. Not a conversation.

The conversation is an output of a run, not an input to it. Every scalability problem above traces back to the Story currently containing fragments of the transcript. Delete that and most of them dissolve.

Four parts, and note what's absent:

- id: hi-005
  labels: {intent: pay_claim, policy: HI-P4, persona: cooperative, channel: chat, locale: en}

  situation:                          # the world, declaratively — no ordering
    seed_delta: {}                    # story-specific state on top of the pack seed
    actors:
      member:
        identity: MEM-0001
        knows: {name: Ada Okafor, service: D2740, billed: 800}
        wants: file the crown claim, get it approved, be told the amount
        consents_to: [{action: pay_claim, once: true, for: $claim}]
        refuses: [revealing other members' data]
    faults: []                        # deterministic API misbehaviour

  constraints:                        # true regardless of path
    invariants: [verify_member_before_claim, payout_within_annual_cap, confirm_before_paying]
    ordering: [[approve_claim($claim), pay_claim($claim)]]
    limits: {pay_claim: 1, model_calls: 20}
    establishes: [{information: member_identity, via: [get_member, list_members]}]

  outcomes:                           # pass if ANY is fully satisfied
    - name: paid after consent
      expect: [{resource: claim, op: created, bind: $claim, fields: {status: paid, approved_amount: 720}}]
    - name: approved, member declined
      expect: [{resource: claim, op: created, bind: $claim, fields: {status: approved}}]

No user_turns. No after_call. No must_call. No literal ids.

The user's knowledge as a fact base rather than a script is the keystone move — the same role references played for the pack. It's what makes unexpected questions, reordered questions, and different planning strategies work naturally instead of needing a case for each: the agent asks whatever it wants, whenever it wants, and the simulator answers from facts or says "I don't know."

Honest trade-offs of that design

1. The stimulus becomes non-deterministic. This is the real cost. Today's scripted turns mean variance across trials is purely the agent's — which is what makes pass^k clean. A fact-driven simulator adds a second variance source. Mitigation: dual-mode (frozen scripts for the regression baseline, fact-base for exploration) and report agent-variance and user-variance separately. I don't think this is fully solvable, only managed.
2. "Any outcome passes" can be abused. Needs the trivial-story detector extended to outcome sets.
3. Diagnosability drops. "It missed all three outcomes" is useless without reporting which it was closest to and by what diff. That reporting is now mandatory, not nice-to-have.
4. More concepts. Mitigated by deleting three fields and by facts being easier to author than scripts — and by the pipeline being able to generate facts from the tool graph.

What I'd actually do, in order

Now, cheap, high value:
1. Fix the elif bug — the user going silent and the story silently weakening is a correctness issue, not a design preference
2. bind / symbolic ids — kills a whole class of silent breakage before the suite grows
3. limits (call counts + model calls) — catches double-charge and cost regression
4. labels replacing the fixed triple

Next, real design work:
5. outcomes — we've already been bitten
6. must_call → establishes with any_of, then delete must_call
7. Fault injection — biggest missing category

Deliberately deferred, eyes open: actors/multi-session, async events, cross-domain composition, non-terminal scoring.

The one-line test for whether the abstraction is healthy: can I author this story without knowing which tools the agent will call, or in what order? Today the answer is no. That's the whole problem, and it's fixable without touching the twin, the scorer's determinism, or the regression machinery.

One aside: I can see the key was rotated — good, but the new one has now landed in this transcript too, so it'll need the same treatment when you're done.

✻ Churned for 4m 13s

※ recap: You're building Kanon, a spec-to-deterministic-twin agent testing gate, and we just finished reviewing whether the Story abstraction scales. Next action is your call on whether to implement the Story fixes I ranked, starting with the user-turn silent-degradation bug. (disable recaps in /config)
                                                                                                                                             97% context used
────────────────────────────
