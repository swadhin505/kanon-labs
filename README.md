# Kanon Labs

Kanon tests agents against a deterministic, stateful twin and fails CI when a
specific intent × policy × persona slice becomes less reliable.

The scorer is deterministic: expected state changes, required tool calls, and
Python policy invariants decide pass/fail. An LLM may be the agent under test,
but no LLM judges the result.

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
`file_claim / HI-P3 / adversarial`: the agent approved more than the annual
cap after the user pushed back. That failure is the demo succeeding.

Open the same evidence in the dashboard:

```powershell
cd ui
npm ci
npm run dev
```

Visit <http://localhost:3000>. Open `hi-002` to see the transcript, tool
request/response, state diff, and invariant linked to the exact violating step.

No API key is needed for this scripted demo.

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
