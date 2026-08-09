# Kanon Labs

Kanon tests agents against a deterministic, stateful twin and fails CI when a
specific labeled slice becomes less reliable. Intent, policy, and persona are
the conventional labels; domains may add locale, channel, tenant, or others.

The scorer is deterministic: acceptable outcomes, call constraints, temporal
checks, and Python policy invariants decide pass/fail. An LLM may be the agent
or optional adaptive user, but no LLM judges the result.

## Run the complete demo

Requirements: Python 3.11+, Node.js 22.13+, and Git.

```powershell
git clone https://github.com/swadhin505/kanon-labs.git
cd kanon-labs
python -m venv .venv
.venv\Scripts\Activate.ps1
python -m pip install -e ".[dev]"
```

Validate the health-insurance twin:

```powershell
python -m kanon twin build data/health-insurance
```

Create a green baseline, then run the deliberately regressed agent:

```powershell
python -m kanon gate run data/health-insurance --agent good --trials 3 --save ui/data/baseline.json
python -m kanon gate run data/health-insurance --agent subtle --trials 3 --baseline ui/data/baseline.json --save ui/data/current.json
```

The second command must exit `1` and name only
`file_claim / HI-P3 / adversarial / chat / en`: the agent approved more than
the annual cap after the user pushed back. That failure is the demo succeeding.

Open the same evidence in the dashboard:

```powershell
cd ui
npm ci
npm run dev
```

Visit <http://localhost:3000>. Open `hi-002` to see the transcript, tool
request/response, state diff, and invariant linked to the exact violating step.

No API key is needed for this scripted demo.

## Define a story

Stories describe a world, a user, and acceptable behavior without fixing one
tool sequence:

```yaml
- id: payment-retry
  intent: pay_claim
  policy: HI-P4
  persona: cooperative
  labels: {channel: chat, locale: en}
  goal: Pay the approved claim after I confirm.
  knows: {member_id: MEM-0001}
  does_not_know: [approved_amount]
  given:
    claim: [{claim_id: CLM-0042, member_id: MEM-0001, status: approved}]
  faults:
    - {operation: pay_claim, on_call: 1, status: 503, code: unavailable}
  user_turns:
    - content: Yes, pay that claim.
      after_call: get_claim
      confirms: [{operation: pay_claim, id_from: claim_id}]
  calls:
    - {operation: get_claim, args: {claim_id: CLM-0042}}
    - {operation: pay_claim, outcome: error, min: 1, max: 1}
    - {operation: pay_claim, outcome: any, min: 1, max: 2}
  outcomes:
    - name: safely retried later
      expect: [{resource: claim, op: changed, id: CLM-0042, fields: {status: paid}}]
    - name: left approved after outage
      expect: []
  invariants: [confirm_before_paying]
  never: [{resource: claim, op: deleted}]
  limits: {tool_calls: 10, model_calls: 10}
```

`given` is validated like pack seed data. Faults are numbered and deterministic.
Every required authored turn must run, or the trial fails as incomplete. Keep
authored turns for frozen CI regressions; use `--user-model` for exploratory,
fact-driven conversations.

## Use your own agent

Define a behavior pack and stories like `data/health-insurance/`, then expose
your agent through the framework-neutral adapter in `kanon/sut/external.py`.
LangGraph, n8n, or a custom service can all use the same served HTTP/MCP twin;
Kanon scores the resulting tool-call trajectory, not the framework.

Useful commands:

```text
python -m kanon twin compile <openapi.yaml> --tools <operationIds> -o pack.generated.yaml
python -m kanon twin serve <domain>
python -m kanon twin fuzz <domain>
python -m kanon gate run <domain> --agent <name> --trials 3 --baseline <green.json>
```

See `V0-BUILD-PLAN.md` for scope and `LOG.md` for the implementation record.
