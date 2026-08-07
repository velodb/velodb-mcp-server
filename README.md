<!--
Licensed to the Apache Software Foundation (ASF) under one
or more contributor license agreements.  See the NOTICE file
distributed with this work for additional information
regarding copyright ownership.  The ASF licenses this file
to you under the Apache License, Version 2.0 (the
"License"); you may not use this file except in compliance
with the License.  You may obtain a copy of the License at

  http://www.apache.org/licenses/LICENSE-2.0

Unless required by applicable law or agreed to in writing,
software distributed under the License is distributed on an
"AS IS" BASIS, WITHOUT WARRANTIES OR CONDITIONS OF ANY
KIND, either express or implied.  See the License for the
specific language governing permissions and limitations
under the License.
-->

# VeloDB MCP Server

VeloDB MCP Server is a backend service that exposes [VeloDB](https://www.velodb.com/) through the [Model Context Protocol (MCP)](https://modelcontextprotocol.io/). AI clients (Claude Desktop, Cursor, VS Code, and others) can query VeloDB data through a governed **semantic metrics layer** built on [MetricFlow](https://github.com/dbt-labs/metricflow), with raw-SQL discovery as a fallback path. It ships with a Web UI for managing semantic models and a CLI client for scripting.

## Core Features

*   **Semantic Metrics Layer**: Define metrics once in YAML (simple / ratio / derived / cumulative / conversion), query them from any MCP client. MetricFlow compiles semantically correct SQL — no hand-written aggregation queries.
*   **Multi-Workspace Isolation**: Fully isolated tenants with their own models, compiler, and VeloDB storage tables. Models are stored in VeloDB itself (`active` + `staging` tables), so multiple server nodes share state without file sync.
*   **Staging Workflow**: All model changes go through *staging → validate → commit*; broken models can never affect running queries.
*   **Guided Tooling**: 10 MCP tools with an enforced workflow (`get_query_guide` → `check_service_health` → semantic query, or metadata discovery → raw SQL fallback).
*   **Credential Pass-Through**: `Authorization: Bearer <velodb-user>:<password>` — every query runs under the caller's own VeloDB identity with per-user connection pools. No shared admin credentials.
*   **Web UI**: Login with VeloDB credentials to edit/validate/publish models, manage workspaces, and deploy the bundled example — no YAML tooling required.
*   **CLI Client**: `mcp-client` for calling tools and pushing/pulling model files from scripts and CI/CD.
*   **Multi-Node Ready**: Session affinity is handled in-app using the server IP recorded in the Web UI cookie; nginx stays a plain reverse proxy.
*   **Self-Contained Packaging**: Release tarballs bundle a Python 3.10 runtime and all dependencies. No network, no pip, no system Python required on target machines.

## System Requirements

*   **Server host**: Linux x86_64 or ARM64 (for running the release package)
*   **Database**: any MySQL-protocol-compatible Doris-family cluster — VeloDB Cloud, VeloDB Enterprise, or Apache Doris — with the FE reachable (default `127.0.0.1:9030`)
*   **Building from source**: curl/wget, or a local Python 3.10.x for offline builds

## Supported Databases

All Doris-family databases speak the same MySQL protocol, so this server works identically with all three:

| Database | Deployment | How to connect |
|----------|-----------|----------------|
| **VeloDB Cloud** | Fully managed cloud service | Use your instance endpoint (e.g. `xxx.us-east-1.aws.velodb.io`) with port `9030` |
| **VeloDB Enterprise** | Self-hosted / on-premise | Use any FE host (IP or hostname) with its MySQL port (default `9030`) |
| **Apache Doris** | Open-source, self-hosted | Use any FE host (IP or hostname) with its MySQL port (default `9030`) |

Just point `server.fe_host` / `server.fe_port` in `mcp-server.toml` at your cluster — no other configuration differs between them.

## 🤖 Install with an AI Agent (no docs reading required)

If you use Claude Code (or another MCP-capable agent), you don't need to read
this README at all. Just paste this into your agent:

> **"Install this MCP server per this README and tell me how to connect my VeloDB cluster."**

The agent will:

1. Ask you 2 questions — your **VeloDB connection address and credentials**
   (works the same for VeloDB Cloud, VeloDB Enterprise, and Apache Doris,
   all of which speak the MySQL protocol), and **where to install**.
2. Download the matching release (self-contained: Python runtime and all
   dependencies are bundled — nothing else to install).
3. Configure `mcp-server.toml`, start the server, and health-check it.
4. Register itself as the `velodb` MCP server in your AI client.
5. Verify the connection end-to-end and confirm you can start querying.

The agent-facing step-by-step playbook (including failure handling) lives in
[AGENTS.md](AGENTS.md). Agents reading this README should follow that file.

## 🚀 Quick Start

### 1. Get the package

Download the latest release from [Releases](../../releases):

```bash
tar xzf velodb-mcp-server-<version>-linux_x64.tar.gz
cd velodb-mcp-server
```

Or build from source (see [Building from Source](#building-from-source)).

### 2. Start the server

The default points to a same-host VeloDB FE (`127.0.0.1:9030`). For a separate deployment, set `server.fe_host` to the FE private IP or hostname, then:

```bash
# Foreground
./start-mcp-server.sh

# Background
nohup ./start-mcp-server.sh > /dev/null 2>&1 &
```

The server listens on port **3000** by default.

### 3. Connect your MCP client

Authentication is your **VeloDB username and password** passed as a Bearer token:

```text
Authorization: Bearer <velodb-user>:<velodb-password>
```

**Claude Desktop / Claude Code:**

```bash
claude mcp add --transport http velodb http://<host>:3000/mcp \
  --header "Authorization: Bearer <user>:<password>"
```

**Cursor / VS Code (`mcp.json`):**

```json
{
  "mcpServers": {
    "velodb": {
      "type": "http",
      "url": "http://<host>:3000/mcp",
      "headers": {
        "Authorization": "Bearer <user>:<password>"
      }
    }
  }
}
```

A ready-to-copy template is provided at [`mcp.json.example`](mcp.json.example).

**Smoke-test the connection with the FastMCP CLI:**

```bash
fastmcp call http://<host>:3000/mcp check_service_health \
  --auth "<user>:<password>" --json
```

### 4. Deploy the example workspace (Web UI)

1. Open `http://<host>:3000/mcp/web` and log in with your VeloDB credentials (management operations require the VeloDB `admin` user).
2. Click the **example deploy** button. Deployment runs in the background; the page polls progress and redirects when done.
3. Back in your AI client, ask: *"What is the total order amount by channel?"* — the agent will discover the `example` workspace and query metrics like `total_amount` grouped by `channel`.

### 5. Manage semantic models

**Web UI** (`/mcp/web`): create/upload/edit YAML models → **Validate** → **Commit**. Only validated models go live.

**CLI client:**

```bash
export VELODB_MCP_SERVER=http://<host>:3000
export VELODB_MCP_TOKEN=<user>:<password>

./mcp-client.sh semantic push ./models -w my_workspace
./mcp-client.sh semantic pull -o ./backup -w my_workspace
./mcp-client.sh tool call list_metrics --json '{"workspace":"my_workspace"}'
```

## How the Agent Queries Data

```
get_query_guide()              ← 1. workflow instructions (always first)
check_service_health()         ← 2. VeloDB connectivity + workspace status
    │
    ├─ semantic layer healthy ─→ list_metrics → list_dimensions_for_metric → query_metric
    │                             (counts, sums, ratios, rankings, trends)
    └─ no matching metric ─────→ list_databases → list_tables → describe_table → execute_query
                                  (raw SQL fallback, read-only validated)
```

## Configuration (`mcp-server.toml`)

| Key | Default | Description |
|-----|---------|-------------|
| `server.mcp_host` / `server.mcp_port` | `0.0.0.0` / `3000` | HTTP listen address |
| `server.fe_host` / `server.fe_port` | `127.0.0.1` / `9030` | VeloDB FE endpoint; set a private IP or hostname for remote FE deployment |
| `query.db_whitelist` | `[]` | Optional database allow-list |
| `query.query_timeout_seconds` | `600` | SQL query timeout |
| `query.query_max_rows` | `10000` | Max rows per query |

All values support `${ENV_VAR}` interpolation.

## Multi-Node Deployment

Multiple server nodes behind one load balancer work without an affinity configuration. On the first successful Web UI login, a concrete `server.mcp_host` is written directly into the session cookie; when listening on `0.0.0.0`, the server instead reads the local IPv4 address on which the request arrived. The cookie format is `session_id.<server-ip>`. If a later request reaches another node, the middleware forwards it to the node recorded in the cookie. nginx needs no cookie parsing, and `/mcp` traffic stays node-local. See [DESIGN.md](DESIGN.md) §8.3 for details.

## Building from Source

```bash
./build.sh linux-x64      # Linux x86_64
./build.sh linux-arm64    # Linux ARM64
./build.sh macos-arm64    # macOS Apple Silicon
./build.sh clean          # remove build artifacts
```

The build downloads a standalone Python 3.10 and produces a self-contained tarball in `dist/`. If GitHub is unreachable, point to a local Python 3.10:

```bash
VELODB_MCP_SYSTEM_PYTHON=/opt/miniconda3/bin/python ./build.sh linux-x64
```

**CI releases** (`.github/workflows/release.yml`): pushing a tag named `velodb-mcp-server-x.y.z` builds linux-x64 + linux-arm64 packages and publishes a GitHub Release for that version. A release can also be triggered manually from the Actions page.

## Running Tests

```bash
bash test/run_all_tests.sh --offline   # unit tests, no server needed
bash test/run_all_tests.sh --smoke     # quick smoke test
bash test/run_all_tests.sh             # full suite (needs a local server)
```

## Documentation

*   [AGENTS.md](AGENTS.md) — agent-facing installation playbook (AI agents read this to install everything automatically)
*   [DESIGN.md](DESIGN.md) — architecture and design decisions
*   [INSTALL.html](INSTALL.html) — installation guide
*   [velodb-mcp-docs.html](velodb-mcp-docs.html) — semantic model reference and user guide

## License

Licensed under the [Apache License, Version 2.0](LICENSE.txt).
This distribution vendors MetricFlow (`src/metricflow/`), which carries its own license — see `src/metricflow/LICENSE` and `src/metricflow/NOTICE`.
