# Agent Tune

[![System Design](https://img.shields.io/badge/docs-System%20Design-blue)](docs/DESIGN.md)

Evaluating tool-using AI agents is hard. You need real APIs, real users, and real traffic -- none of which are available at development time. Agent Tune solves this by generating benchmark scenarios from your tool specifications, emulating your APIs with cross-call consistency, and orchestrating synthetic conversations across diverse user personas.

Given an OpenAPI spec (or MCP servers, or Python tool definitions), Agent Tune discovers tool dependencies, identifies user intents, extracts domain policies, and produces coverage-driven user stories that stress-test your agent across happy paths, policy violations, and edge cases.

## The Pipeline

Every implementation of Agent Tune shares the same core build pipeline:

```
Tool Specs ──> Tool Graph ──> Guidance ──> Policies ──> User Stories
               (dependencies)  (intents,    (domain      (benchmark
                               slots,       rules,       scenarios with
                               tool chains) violations,  personas and
                                            edge cases)  coverage matrix)
```

**Tool Graph** -- Rule-based dependency analysis of parameter flow between tools. No LLM required.

**Guidance** -- Discovers user intents, required/optional slots, and tool-intent mappings. Validated against the tool graph.

**Policies** -- Extracts domain rules with preconditions, postconditions, violation conditions, and edge cases. Each policy drives three story paths: happy path, violation attempt, and edge case.

**User Stories** -- Coverage-driven benchmark scenarios. Each story includes known/unknown info, expected actions, expected outcomes, persona assignment (cooperative, confused, adversarial), and policy references. Stratified sampling ensures broad coverage across the intent x policy x persona matrix.

The SaaS variant extends this with runtime capabilities: LLM-powered tool emulation, persona-driven user simulation, and automated experiment orchestration with observability.

## Three Ways to Use It

Agent Tune is available in three forms, each suited to different workflows:

| | SaaS Service | Claude Code Plugin | IBM Bob Mode |
|---|---|---|---|
| **What** | Multi-tenant FastAPI + Next.js dashboard | Skills, commands, agents, and tools for Claude Code | Custom mode with rules for IBM Bob |
| **Where** | `src/agent_tune/`, `ui/` | `marketplace/plugins/agent-tune/` | `ibm-bob-mode/` |
| **Scope** | Full pipeline + runtime emulation + experiment orchestration | Build pipeline (user story generation) | Build pipeline (user story generation) |
| **Style** | Managed service, human-implemented with LLM-assisted workflows | LLM-orchestrated -- Claude Code runs the pipeline autonomously | LLM-orchestrated -- Bob runs the pipeline autonomously |
| **Best for** | Teams running experiments at scale | Developers who live in their editor (Claude Code) | Developers who live in their editor (IBM Bob) |
| **Get started** | [SaaS Quick Start](#saas-quick-start) below, [UI README](ui/README.md) | [Plugin README](marketplace/plugins/agent-tune/README.md) | [Bob Mode README](ibm-bob-mode/README.md) |

## SaaS Quick Start

### 1. Setup Environment

```bash
# Clone and install
git clone <repo-url>
cd agent-tune
uv venv --python 3.12
source .venv/bin/activate
uv pip install -e ".[dev,test-documents]"

# Configure environment
cp .env.example .env
# Edit .env with your keys (and model overrides if needed), e.g.:
#   MODEL_API_KEY=<your-openai-api-key>
#   WX_AGENT_BASE_URL=<your-watsonx-orchestrate-url>
#   WX_AGENT_API_KEY=<your-watsonx-api-key>
```

### 2. Run the Agent Tune Service

```bash
agent-tune service run --data-root ./output --port 8000
```

The service exposes:

- `POST /tenants` - Create tenant workspaces
- `POST /tenants/{id}/experiments` - Run experiments
- `GET /tenants/{id}/experiments/{exp_id}` - Check experiment status
- `POST /tenants/{id}/tool-call` - Tool emulation endpoint
- `GET /tenants/{id}/wrappers` - HTTP facade for agent tool calls

### 3. Create a Public Tunnel

Your AI agent (e.g., on WatsonX Orchestrate) needs to reach the Agent Tune service to execute tool calls. Create a tunnel:

```bash
# Install cloudflared if needed
brew install cloudflare/cloudflare/cloudflared

# Create tunnel
cloudflared tunnel --url http://localhost:8000
```

> **Note the public URL (e.g., `https://<random-name>.trycloudflare.com`) - you'll use it when running experiments.**

### 4. Try the Health Insurance Example

The `data/health-insurance/` directory contains a complete working example.

#### Step 4a: Deploy Agent to WatsonX Orchestrate (WxO)

```bash
./data/health-insurance/import-tools-and-agent.sh
```

This script:

- Installs the `ibm-watsonx-orchestrate` CLI
- Registers tools from [tools.yaml](data/health-insurance/v2/tools.yaml) into your WxO instance
- Creates and deploys the agent specified in [agent.yaml](data/health-insurance/v2/agent.yaml)

#### Step 4b: Create the Agent Tune Tenant

```bash
./data/health-insurance/create-tenant.sh
```

This creates a tenant workspace using the provided API spec, samples, and tool graph config. It performs:

- Tool graph analysis (discovers tool dependencies)
- Guidance generation (identifies intents and workflows)
- Emulator artifacts (canonicalization rules, integrity constraints, seed memory)

Input files for our health insurance example are in [data/health-insurance/v2](data/health-insurance/v2):

- [api.json](data/health-insurance/v2/api.json) - OpenAPI specification
- [optional] [api_samples.json](data/health-insurance/v2/api_samples.json) - Example request/response pairs
- [optional] [config.yaml](data/health-insurance/v2/config.yaml) - Information available to the agent outside of tools (from the context)
- [optional] [tool_graph_config.yaml](data/health-insurance/v2/tool_graph_config.yaml) - Synonym mappings for tool dependency detection

#### Step 4c: Run an Experiment

> **Make sure your tunnel is running and note the URL!**

```bash
# Set your tunnel URL
export TUNNEL_SERVER=https://<your-random-tunnel-server-name>.trycloudflare.com

# Run the experiment
./data/health-insurance/run-experiment.sh
```

This:

- Connects to your deployed WxO agent
- Modifies the tools to use Agent Tune tool wrappers endpoint (which will route all the agent tool calls through to Agent Tune's tool emulator)
- Generates synthetic user conversations across different intents and personas using the WxO Agent (via WxO API), and Agent Tune's user emulator API
- Saves experiment results and logs to `tenants/health-insurance/experiments/`
- Agent trajectories for the conversations during the experiment can be retrieved using the observability settings of WxO.

#### Step 4d: Check Experiment Status

```bash
# Replace <experiment_id> with the ID printed when you ran the experiment
curl http://localhost:8000/tenants/health-insurance/experiments/<experiment_id>
```

#### Step 4e: Get Agent Trajectories

Agent conversations (user and agent messages) are available in `output/tenants/health-insurance/experiments/<experiment_id>/runs`.

For full agent trajectories including all LLM calls, tool calls, and metadata, access the WatsonX Orchestrate observability features or the Langfuse dashboard.

You may download traces from Langfuse directly, or use the export script:

```bash
# Add Langfuse details to .env
LANGFUSE_PUBLIC_KEY=<your-langfuse-public-key>
LANGFUSE_SECRET_KEY=<your-langfuse-secret-key>
LANGFUSE_BASE_URL=<your-langfuse-base-url>
LANGFUSE_TIMEOUT_SECONDS=120

# Export traces
python scripts/langfuse_trace_exporter.py \
  "2025-12-31T04:55:00Z" \
  --out exports
```

## Configuration

### Environment Variables

| Variable | Description | Required |
| --- | --- | --- |
| `MODEL_API_KEY` | OpenAI API key | Yes |
| `DEFAULT_MODEL` | LLM model name | No (default: `gpt-5-mini`) |
| `WX_AGENT_BASE_URL` | WxO instance URL ([how to find](#finding-your-watsonx-orchestrate-api-url)) | For experiments |
| `WX_AGENT_API_KEY` | IBM Cloud IAM API key ([how to create](#creating-an-ibm-cloud-iam-api-key)) | For experiments |

### Finding Your WatsonX Orchestrate API URL

To use Agent Tune with IBM WatsonX Orchestrate, you need a service instance URL.

#### Create a new instance

1. Go to the [IBM Cloud Catalog - watsonx Orchestrate](https://cloud.ibm.com/catalog/services/watsonx-orchestrate)
2. Select your pricing plan and create the service
3. Once provisioned, find your API URL in the service credentials

#### Find URL for an existing instance

1. Log into your watsonx Orchestrate account
2. Click your profile icon -> **Settings**
3. Navigate to the **API details** tab
4. Copy the **Service instance URL**

The URL format varies by deployment:

| Deployment | URL Format |
| --- | --- |
| IBM Cloud | `https://api.<region>.watson-orchestrate.cloud.ibm.com/instances/<tenant_id>` |
| AWS | `https://api.<region>.watson-orchestrate.ibm.com/instances/<tenant_id>` |

For more details, see the [official IBM documentation](https://www.ibm.com/docs/en/watsonx/watson-orchestrate/base?topic=api-getting-endpoint).

### Creating an IBM Cloud IAM API Key

Agent Tune needs IBM Cloud IAM API keys to authenticate with WatsonX Orchestrate.

#### Via IBM Cloud Console

1. Go to [IBM Cloud Console](https://cloud.ibm.com)
2. Navigate to **Manage** -> **Access (IAM)** -> **API keys**
3. Click **Create +**
4. Copy the key immediately (it won't be shown again)

#### Via CLI

```bash
ibmcloud login --sso
ibmcloud iam api-key-create my-agent-tune-key --output json
```

For more details, see the [IBM Cloud IAM API keys documentation](https://cloud.ibm.com/docs/account?topic=account-userapikey&interface=ui).

### Service Options

```bash
agent-tune service run \
  --data-root ./output \     # Where tenant data is stored
  --port 8000 \              # Listen port
  --host 0.0.0.0             # Bind address
```

### Local Langfuse for Observability

Agent Tune can automatically start a local [Langfuse](https://langfuse.com/) instance for trace observability:

```bash
agent-tune service run --data-root ./output --port 8000 --start-langfuse
```

This starts Langfuse via Docker Compose and makes it available at `http://localhost:3000`.

**Default credentials (created on first startup):**

| Resource          | Value                              |
|-------------------|------------------------------------|
| Organization      | `agent-tune`                       |
| Project           | `default`                          |
| Public key        | `lf_pk_agent_tune_local`           |
| Secret key        | `lf_sk_agent_tune_local_secret`    |
| Admin user        | `admin@agent-tune.local`           |
| Admin password    | `agent-tune-admin`                 |

**Using the API keys:** Add these to your `.env` file for tracing:

```bash
LANGFUSE_BASE_URL=http://localhost:3000
LANGFUSE_PUBLIC_KEY=lf_pk_agent_tune_local
LANGFUSE_SECRET_KEY=lf_sk_agent_tune_local_secret
```

**Prerequisites for `--start-langfuse`:**

| Requirement                       | Notes                                                                        |
|-----------------------------------|------------------------------------------------------------------------------|
| Docker Desktop or Rancher Desktop | Must be running before starting the service                                  |
| Docker Compose                    | Included with Docker Desktop and Rancher Desktop                             |
| 4+ vCPUs, 16GB+ RAM               | Langfuse runs 6 containers (web, worker, postgres, clickhouse, redis, minio) |

**First run**: Initial startup takes 2-3 minutes to download container images. Subsequent starts are much faster.

**Data persistence**: When you stop the service (Ctrl+C), Langfuse containers are stopped but data volumes are preserved. Your traces, projects, and API keys persist across restarts.

**Manual Langfuse management** (if needed):

```bash
# View Langfuse logs
docker compose -f docker/langfuse-compose.yml logs -f

# Stop Langfuse manually
docker compose -f docker/langfuse-compose.yml down

# Reset Langfuse (delete all data)
docker compose -f docker/langfuse-compose.yml down -v
```

## Project Structure

```text
agent-tune/
├── src/agent_tune/               # Core Python package
│   ├── cli/                      #   Typer CLI
│   ├── service/                  #   FastAPI multi-tenant service
│   │   ├── tenants/              #     Tenant management
│   │   ├── emulator/             #     Tool & user emulation
│   │   ├── experiments/          #     Experiment orchestration
│   │   └── runtime/              #     Runtime routes
│   ├── api_spec/                 #   OpenAPI spec generation
│   ├── tool_graph/               #   Dependency graph (rule-based)
│   ├── guidance/                 #   Intent/slot discovery
│   ├── user_stories/             #   TAU-GEN benchmark generation
│   ├── tool_emulator/            #   Tool emulation artifacts
│   ├── tool_simulator/           #   Story-driven tool simulation
│   ├── user_simulator/           #   Story-driven user simulation
│   ├── optimization/             #   Trajectory fault detection
│   └── mcp_server/               #   MCP server for IDE integration
├── ui/                           # Next.js web dashboard
├── marketplace/                  # Claude Code Plugin
│   └── plugins/agent-tune/       #   Skills, commands, agents, tools
├── ibm-bob-mode/                 # IBM Bob custom mode
│   ├── custom_modes.yaml         #   Mode definition
│   └── .bob/rules-agent-tune/    #   Reference material
├── docs/                         # Design docs (SDLC-style)
├── tests/                        # pytest test suite
├── examples/                     # Working examples
├── scripts/                      # Utility scripts
├── data/                         # Example tenant data
│   └── health-insurance/         #   Complete working example
├── docker/                       # Docker Compose for local Langfuse
├── gepa/                         # [External submodule] GEPA optimization
├── tau2-bench/                   # [External submodule] TAU2 benchmark framework
└── pyproject.toml                # Python package config (hatchling, uv-managed)
```

## Documentation

- **[System Design](docs/DESIGN.md)** - Comprehensive architecture documentation with Mermaid diagrams
- **[UI README](ui/README.md)** - Next.js dashboard setup and development
- **[Claude Code Plugin README](marketplace/plugins/agent-tune/README.md)** - Plugin installation, commands, and reference
- **[IBM Bob Mode README](ibm-bob-mode/README.md)** - Mode installation, usage, and configuration
- **[TAU-GEN Design](docs/tau-gen/)** - User stories pipeline design docs