# DESIGN.md — velodb-mcp-server Design Document

## Overview

**velodb-mcp-server** is a VeloDB query service based on the MCP (Model Context Protocol). It exposes VeloDB data query capabilities through FastMCP's streamable-http transport, ships with a semantic metrics layer built on MetricFlow v0.209.0, supports multi-workspace isolation, and provides both a Web UI and a CLI for administration.

```
                     MCP protocol (streamable-http, stateless)
┌──────────────────────────────────────────────────────────────────┐
│                       AI clients (LLM)                           │
│    Claude Desktop / Cursor / VeloDB / Codex / custom clients     │
└─────────────────────────────┬────────────────────────────────────┘
                              │
                              ▼
┌──────────────────────────────────────────────────────────────────┐
│                     FastMCP 3.x server                           │
│                                                                  │
│  ┌───────────────┐  ┌──────────────┐  ┌────────────────────────┐ │
│  │  10 Tools     │  │  Web UI      │  │  REST API              │ │
│  │  (LLM calls)  │  │  /mcp/web/*  │  │  /mcp/web/semantic/*   │ │
│  └───────┬───────┘  └──────┬───────┘  └───────────┬────────────┘ │
│          │                 │                       │              │
│  ┌───────┴─────────────────┴───────────────────────┴────────────┐ │
│  │                    Authentication layer                      │ │
│  │  MCP:  Bearer username:password → CredentialVerifier → VeloDB │ │
│  │  Web:  session cookie <session_id>.<node-IP> (24h TTL,       │ │
│  │        httponly)                                             │ │
│  │  Cache: 10-minute in-memory credential cache; login          │ │
│  │         brute-force lockout (5 attempts / 5 minutes)         │ │
│  │  Connections: per-user aiomysql pools (no shared admin pool) │ │
│  └───────────────────────────┬──────────────────────────────────┘ │
│                              │                                    │
│  ┌───────────────────────────┴──────────────────────────────────┐ │
│  │                 Multi-workspace manager                      │ │
│  │  Per workspace: Store → Manifest → Compiler (MetricFlow)     │ │
│  │  60s polling for changes                                     │ │
│  │  Auto-discovers added/removed workspaces                     │ │
│  │  MetricRouter: metric_name → (compiler, workspace)           │ │
│  └───────────────────────────┬──────────────────────────────────┘ │
└──────────────────────────────┼────────────────────────────────────┘
                               │ pymysql / aiomysql
                               ▼
                    ┌─────────────────────┐
                    │      VeloDB FE       │
                    │   127.0.0.1:9030    │
                    │                     │
                    │  system_mcp.*       │  ← workspace storage
                    │  dw.*               │  ← user data tables
                    └─────────────────────┘
```

---

## 1. Entry Point and Lifecycle

### 1.1 Startup flow (`src/main.py`)

```
main()
  ├─ Parse arguments (--config-dir, --env-file)
  ├─ AppConfig.load(mcp-server.toml)   ← TOML config file, supports ${VAR} env interpolation
  ├─ resolve_machine_ip()              ← fallback node IP when the request address is unavailable
  └─ create_server()
       ├─ MultiWorkspaceWatcher         ← lazy init: scans workspaces on first authenticated request
       ├─ PoolManager                   ← per-user aiomysql pool factory (no shared admin pool)
       ├─ CredentialVerifier            ← Bearer token → VeloDB credential verification (10-min cache)
       ├─ Register 10 MCP Tools
       ├─ Register Web UI routes (/mcp/web/*)
       └─ Register REST API routes (/mcp/web/semantic/*, /mcp/web/staging/*)
           ↓
mcp.run(transport="streamable-http", stateless_http=True, port=3000,
        middleware=[
          RequestLoggerMiddleware,        ← request/response logging (sensitive data masked)
          SessionAffinityProxyMiddleware, ← Web UI session affinity (see §8.3)
          CharsetMiddleware,              ← charset handling
        ])
```

### 1.2 Shutdown

The `lifespan` context manager releases all connection pools when the server shuts down.

---

## 2. Configuration (`src/config/loader.py`)

### 2.1 `mcp-server.toml`

```toml
[server]
mcp_name = "velodb-mcp-server"      # MCP server name
mcp_host = "0.0.0.0"            # Listen address
mcp_port = 3000                 # HTTP port
fe_port = 9030                  # VeloDB FE MySQL port (same-host 127.0.0.1)

[logging]
level = "info"                  # debug|info|warning|error
audit_log = "./logs/audit.log"  # Audit log path
rotation_when = "midnight"      # Rotate daily
rotation_backup_count = 30      # Keep 30 days

[query]
pool_min_size = 0
pool_max_size = 10
pool_idle_timeout_seconds = 300
query_timeout_seconds = 600
# db_whitelist = ["dw", "system_mcp"]   # Optional: database allow-list restricting accessible databases
query_max_rows = 10000           # Default maximum rows returned
```

### 2.2 Configuration classes

| Class | Responsibility |
|-------|----------------|
| `AppConfig` | Top level; loads TOML/YAML; regex-replaces `${VAR}` environment variables |
| `McpConfig` | Server name, address/port, logging config, seed switch |
| `ClusterConfig` | VeloDB FE connection, pool parameters, query limits, database allow-list |

---

## 3. MCP Tools (10 total)

### 3.1 Tool list

| # | Tool | Annotations | Purpose |
|---|------|-------------|---------|
| 1 | `get_query_guide` | read-only, idempotent | **Mandatory first call.** Returns the full workflow guide telling the AI when to use the semantic layer vs. raw SQL, and the tool call order. |
| 2 | `check_service_health` | read-only, idempotent | **Mandatory second call.** VeloDB connectivity + per-workspace status + metric counts. |
| 3 | `list_metrics` | read-only, idempotent | Lists all metrics (name + description) in a workspace. |
| 4 | `list_dimensions_for_metric` | read-only, idempotent | Returns the available `group_by` dimensions for a metric. |
| 5 | `query_metric` | read-only | **Core query tool.** MetricFlow compile → execute SQL. Supports `metrics`/`group_by`/`where`/`order_by`/`limit`/`having`/`database`/`max_rows`. |
| 6 | `list_databases` | read-only, idempotent | Lists VeloDB databases (paginated). |
| 7 | `list_tables` | read-only, idempotent | Lists tables in a database (supports `like` fuzzy match, paginated). |
| 8 | `describe_table` | read-only, idempotent | Table structure (`names`/`summary`/`full` detail levels). |
| 9 | `execute_query` | read-only | Raw SQL fallback path (only SELECT/SHOW/DESCRIBE/EXPLAIN allowed). |
| 10 | `reload_semantic_layer` | idempotent | Manually triggers a workspace reload. |

### 3.2 Agent-side workflow

The system enforces a strict call order on AI clients:

```
get_query_guide()                    ← Step 1: get the workflow guide
    ↓
check_service_health()               ← Step 2: check VeloDB and workspace status
    ↓
    ├─ Semantic layer healthy? ──→ list_metrics() → list_dimensions_for_metric() → query_metric()
    │                      (normal path: counts, sums, ratios, rankings, trends)
    │
    └─ Semantic layer unavailable or no matching metric?
        └─→ list_databases() → list_tables() → describe_table() → execute_query()
            (fallback path: raw SQL + metadata discovery)
```

**Key rule:** when the semantic layer is healthy and a matching metric exists, bypassing `query_metric` in favor of `execute_query` is never allowed.

### 3.3 Tool implementation pattern

All tools follow a uniform structure:

```python
@mcp.tool(annotations=ToolAnnotations(...))
async def tool_name(param: type, ...) -> str:
    auth = check_tool_access("tool_name")     # 1. authorization
    if auth.denied: return auth.denied
    start = time.monotonic()                  # 2. timing
    pool = await _get_per_user_pool(auth.pool) # 3. acquire connection pool
    result = await _implementation(pool, ...)  # 4. execute
    log_tool_call("tool_name", ..., duration_ms=...) # 5. audit
    return result
```

All results are serialized to JSON via `success_response()` / `error_response()`.

---

## 4. Authentication and Authorization

### 4.1 MCP protocol authentication

```
Authorization: Bearer username:password
```

| Step | Component | Action |
|------|-----------|--------|
| 1 | `CredentialVerifier.verify_token()` | Splits username and password at the first `:` |
| 2 | `CredentialCache` | Checks the 10-minute TTL in-memory cache |
| 3 | `pymysql.connect(host=<fe_host>, user, password)` | Verifies credentials against the configured VeloDB FE |
| 4 | Valid → cache → return `AccessToken` | |
| 5 | Invalid → return 401 | |

Verification uses the machine's **real, non-127.0.0.1 IP** (discovered via a UDP connect to 8.8.8.8), ensuring VeloDB applies the real user identity.

### 4.2 Web UI authentication

```
GET  /mcp/web/login  → render the login form
POST /mcp/web/login  → verify VeloDB credentials → set the "velodb_mcp_session" cookie
                       format: <session_id>.<node-IP> (24h TTL, httponly, samesite=lax)
GET  /mcp/web/logout → clear the session and cookie
```

- **Cookie suffix**: `<node-IP>` is the basis for session-affinity routing (see §8.3); the concrete `mcp_host` takes precedence, and when listening on `0.0.0.0` the IP is taken from the current request's ASGI local socket
- **Brute-force protection**: 5 consecutive failures for the same username locks it for 5 minutes; the lockout presents as "wrong password" so the lockout itself is not disclosed
- **Memory cap**: the session dict is hard-capped at 1000 entries; the oldest session is evicted when exceeded; expired sessions are cleaned up opportunistically at login

### 4.3 Permission model

| Role | Determined by | Permissions |
|------|---------------|-------------|
| **admin** | VeloDB username is exactly `admin` | Everything: upload/pull/validate/commit/discard models, create/delete workspaces, deploy/remove the example, execute arbitrary SQL |
| **Authenticated user** | Valid Bearer token, via `_check_semantic_access()` | Read-only: view models, list/query metrics, execute SQL (read-only validated) |
| **Unauthenticated** | No token | Denied (401 or redirect to the login page) |

### 4.4 Per-user connection pools

Each authenticated user gets an independent `aiomysql` connection pool, connecting via the machine's non-loopback IP. This ensures VeloDB applies user-level authorization correctly. When authentication fails, the credential cache is cleared automatically and the next request re-verifies.

---

## 5. Workspace System

### 5.1 Concept

A **workspace** is a fully isolated logical tenant containing:

- Independent YAML model files
- An independent MetricFlow compiler instance
- An independent metric namespace
- Independent VeloDB storage tables

Metrics in workspace A are **completely invisible** to workspace B.

**Naming rule:** `^[a-zA-Z][a-zA-Z0-9_]*$`

### 5.2 The three workspace states

| State | Meaning | Trigger |
|-------|---------|---------|
| `healthy` | Running normally; metrics queryable | YAML committed successfully, bootstrap parsing passed, MetricFlow engine ready |
| `no_models` | Empty workspace | Newly created, or all files deleted |
| `not_ready` | Load failed | YAML syntax error, table missing, missing project.yaml, MetricFlow validation failure |

```
  no_models  ──upload YAML──→  not_ready  ──fix + commit──→  healthy
      ↑                            ↑                          │
      └──────────────────── upload broken YAML ───────────────┘
```

### 5.3 Storage architecture (`src/store/store.py`)

Each workspace has **two** VeloDB tables in the `system_mcp` database:

```
system_mcp.active_store_{workspace}     ← live models (read-only)
  filename   VARCHAR(512) PRIMARY KEY
  updated_at DATETIME
  content    STRING

system_mcp.staging_store_{workspace}    ← pending changes
  filename   VARCHAR(512) PRIMARY KEY
  action     VARCHAR(16)   -- 'upsert' | 'delete'
  updated_at DATETIME
  content    STRING (NULL for delete)
```

### 5.4 Update flow

```
  User edits YAML (WebUI/CLI)
           │
           ▼
  ┌─────────────────┐
  │  Staging Store  │   ← files enter staging without affecting running queries
  └────────┬────────┘
           │
   ┌───────┼───────┐
   ▼       ▼       ▼
validate commit  discard
   │       │       │
   │  ┌────┴────┐  │
   │  │ Active  │  │
   │  │ Store   │  │
   │  └────┬────┘  │
   │       │       │
   │  auto reload  │
   │  (2-5 sec)    │
   │       │       │
   ▼       ▼       ▼
  ┌─────────────┐
  │   healthy   │
  └─────────────┘
```

**Hard constraint:** validation is required before commit. "Staging must be validated before commit."

### 5.5 Validation pipeline

```
validate_staging(workspace)
  1. staging_fetch()               → merge active + staging into a temp directory
  2. pre_validate_physical()       → YAML syntax, file structure, table existence
  3. bootstrap()                   → MetricFlow build into a temp workspace
  4. SemanticManifest.load()       → parse the generated semantic_manifest.json
  5. _check_staging_duplicates()   → cross-file duplicate measure/model name detection
  6. Return (pass/fail, message, details including the metric list)
```

### 5.6 Multi-workspace manager (`src/store/watcher.py`)

```
MultiWorkspaceWatcher
├─ _init_all()                ← scan active_store_* tables in system_mcp
├─ _poll_loop()               ← background thread, 60s interval
│   ├─ check_remote()         ← detect version changes via revision hash
│   ├─ _reload_workspace()    ← fetch → bootstrap → manifest → compiler
│   └─ discover added/stale workspaces ← scan table changes in system_mcp
├─ MetricRouter               ← metric_name → (compiler, workspace_name)
├─ force_reload()             ← manual reload trigger (API/Tool)
└─ commit_staging()           ← staging_commit() → force_reload()
```

**Atomic swap:** `RWLock.write_acquire()` protects manifest/compiler replacement. No request ever sees a partial state.

---

## 6. Semantic Layer

### 6.1 MetricFlow integration (`src/store/compiler.py`)

```
YAML models (stored in the VeloDB active_store)
      │
      ▼
  bootstrap()          ← MetricFlow build (dbt parsing + manifest generation)
      │
      ▼
  semantic_manifest.json
      │
      ├── SemanticManifest.load()   ← metadata: metrics, dimensions, entities
      │
      └── MetricFlowCompiler
            │
            ├── MetricFlowEngine (compile-only mode)
            │     └── _VeloDBSqlClientStub  ← satisfies the SqlClient interface
            │           used for dialect rendering only; executes no real queries
            │
            └── query_metric() flow:
                  explain(sql) → VeloDB SQL → ConnectionPool.execute(sql) → rows
```

### 6.2 Semantic model structure

A `semantic_model` YAML document contains:

| Field | Required | Description |
|-------|:--:|------|
| `name` | ✅ | Globally unique model name |
| `db_table` | ✅ | VeloDB physical table (`database.table`) |
| `defaults.agg_time_dimension` | ✅ | Default time dimension for metrics |
| `entities` | ✅ | Primary/foreign/unique/natural keys |
| `dimensions` | ✅ | Time dimensions (day/week/month/quarter/year/hour/minute) and categorical dimensions |
| `measures` | recommended | Aggregation definitions (sum/count/count_distinct/average/min/max/median/percentile/sum_boolean) |
| `description` | optional | Free-text description |
| `primary_entity` | conditional | Required when no `type: primary` entity exists |

### 6.3 Advanced metric types

Beyond the simple metrics auto-generated from `measures`, YAML supports four advanced types:

| Type | Purpose | Example |
|------|---------|---------|
| `ratio` | numerator ÷ denominator | conversion rate = orders / visits |
| `derived` | expression over existing metrics | MoM growth, YoY change |
| `cumulative` | accumulation over a time window | last-7-day sales, month-to-date signups |
| `conversion` | user funnel conversion | visit → order conversion rate |

### 6.4 Manifest (`src/store/manifest.py`)

```python
SemanticManifest(semantic_manifest.json)
  .list_metrics()                    # → [{name, description}, ...]
  .get_metric(name)                  # → full metric definition
  .list_dimensions_for_metric(name)  # → [{name, type, description}, ...]
```

---

## 7. Connection Management

### 7.1 Connection pool (`src/core/connection.py`)

```python
ConnectionPool
  ├─ aiomysql.Pool (lazy init, guarded by asyncio.Lock)
  ├─ execute(sql, database, max_rows, timeout) → ([{col: val}, ...], [col_names])
  ├─ Independent per-user pools created via PoolManager (non-127.0.0.1 IP)
  └─ close() → pool.close() + wait_closed()
```

### 7.2 Pool types

| Pool | User | Min/Max | Purpose |
|------|------|---------|---------|
| Per-user pool | `<authenticated user>` | 0/10 | Execute SQL queries as the user |

**No shared admin pool:** all VeloDB connections use the credentials carried by the request (Bearer token or the username/password in the Web UI session); pools for the same user are reused across requests via `PoolManager`. Idle connections are reclaimed per `pool_idle_timeout_seconds`; invalid credentials are automatically evicted from the cache and rebuilt.

---

## 8. Web UI

### 8.1 Route table

| Route | Method | Auth | Purpose |
|-------|--------|------|---------|
| `/mcp/web/login` | GET | None | Login form |
| `/mcp/web/login` | POST | None | Handle login, set session cookie |
| `/mcp/web/logout` | GET | Session | Clear the session |
| `/mcp/web` | GET | Session | Redirect to the model management page |
| `/mcp/web/models` | GET | Session | Live/staged file list + workspace status |
| `/mcp/web/{filename}` | GET | Session | Edit a YAML file |
| `/mcp/web/new` | GET | Admin | New file form |
| `/mcp/web/create` | POST | Admin | Create a new file |
| `/mcp/web/{filename}/save` | POST | Admin | Save the edited file |
| `/mcp/web/{filename}/delete` | GET | Admin | Mark a file for deletion |
| `/mcp/web/upload` | POST | Admin | Upload YAML files (multipart) |

### 8.2 REST API route table

| Route | Method | Auth | Purpose |
|-------|--------|------|---------|
| `/mcp/web/semantic/push` | POST | Admin (Bearer) | CLI: upload YAML (multipart) |
| `/mcp/web/semantic/pull` | GET | Bearer | CLI: download live YAML (.tar.gz) |
| `/mcp/web/semantic/reload` | POST | Admin | HTTP: trigger a workspace reload |
| `/mcp/web/semantic/files` | GET | Bearer | List live files |
| `/mcp/web/semantic/files/{filename}` | GET | Bearer | Get file content |
| `/mcp/web/semantic/files` | POST | Admin | Save a file to staging |
| `/mcp/web/semantic/files/{filename}` | DELETE | Admin | Delete a file from staging |
| `/mcp/web/staging/validate` | POST | Admin | Validate staged changes |
| `/mcp/web/staging/commit` | POST | Admin | Commit to live |
| `/mcp/web/staging/discard` | POST | Admin | Discard staged changes |
| `/mcp/web/workspace/create` | POST | Admin | Create a workspace |
| `/mcp/web/workspace/delete` | POST | Admin | Delete a workspace (DROP storage tables) |
| `/mcp/web/example/deployment` | POST | Admin (WebUI session) | Start an example deploy/remove background task; returns immediately |
| `/mcp/web/example/deployment/status` | GET | Admin (WebUI session) | Poll background task status (idle/running/success/failed) |

### 8.3 Multi-node deployment and session affinity

When multiple MCP Server nodes sit behind one domain (ALB), Web UI sessions are single-node in-memory state, so requests from the same browser must land on the node holding the session. Forwarding is done at the **application layer** by `SessionAffinityProxyMiddleware` (`src/core/session_affinity_proxy.py`); nginx stays a dumb proxy (`proxy_pass http://127.0.0.1:3000`) with no cookie-parsing configuration needed.

**Node address acquisition:** login is handled locally on the node that received the request. On first successful login, if `server.mcp_host` is not `0.0.0.0`, that concrete listening IPv4 is written directly into the `session_id.<node-IP>` cookie; when listening on `0.0.0.0`, the local IPv4 on which the request actually arrived is taken from the ASGI `scope["server"]`. Client-modifiable `Host` or `X-Forwarded-*` headers are not trusted; only when ASGI provides no usable IPv4 does the server fall back to the UDP route-probe result from startup.

**Subsequent routing:** when a request lands on a different node, the middleware parses the cookie suffix IP and forwards via httpx to the node holding the session; when it lands on the node named by the cookie, it is handled locally. The `/mcp` protocol is unaffected and remains node-local.

**Forwarding implementation notes:** shared httpx.AsyncClient (Set-Cookie disabled, no redirect following, trust_env=False); streaming forwarding of request/response bodies; internal hop header `x-velodb-session-affinity-hop` prevents forwarding loops; on upstream timeout/unreachable, the cookie is cleared and the client gets a 303 back to the login page.

---

## 9. CLI Client (`mcp-client/`)

A standalone command-line client distributed as a separate tar.gz package. Connection is configured via environment variables or `velodb-mcp-client.toml`:

```bash
export VELODB_MCP_SERVER=http://<host>:<port>
export VELODB_MCP_TOKEN=admin:admin
```

**MCP Tool calls:**
```bash
velodb-mcp-client tool list
velodb-mcp-client tool call list_metrics --json '{"workspace":"example"}'
velodb-mcp-client tool call query_metric --json '{"metrics":["total_amount"],"group_by":["channel"]}'
```

**Semantic model management:**
```bash
velodb-mcp-client semantic push ./models -w example
velodb-mcp-client semantic pull -o ./backup -w example
velodb-mcp-client semantic list -w example
velodb-mcp-client semantic reload -w example
velodb-mcp-client semantic status
```

---

## 10. Example Workspace

The example is not deployed automatically. An admin can deploy or remove it manually via the dedicated button next to Reload.

**Asynchronous deployment:** deploy/remove is a long-running operation (create database/tables + insert data + GRANT + compile models, which may exceed a proxy/LB's 60s idle timeout). POST `/mcp/web/example/deployment` only starts the background task and returns immediately; the frontend polls GET `.../status` every 2s until success/failed, avoiding the 504 HTML error page a synchronous wait would trigger.

**Example data tables:**

| Table | Rows | Description |
|-------|------|-------------|
| `dw.orders` | 12 | Orders table with order_id/user_id/product_id/amount/channel/status/order_date |
| `dw.users` | 5 | Users table with user_id/name/city/level/register_date |
| `dw.products` | 5 | Products table with product_id/name/category/brand/price |
| `dw.dim_date` | 365 | Date dimension table for time-axis alignment |

**5 example metrics:** `total_amount` (total order amount), `order_count` (number of orders), `avg_amount` (average order value), `unique_users` (unique users), `user_count` (user count)

---

## 11. Key Design Decisions

| Decision | Rationale |
|----------|-----------|
| `stateless_http=True` | No MCP session state is maintained. Compatible with clients that don't keep a session (VeloDB proxy, Claude Desktop). |
| YAML stored in VeloDB | Model files live in VeloDB tables rather than the filesystem. Enables multi-server shared-state deployment without file sync. |
| Two-level storage (active + staging) | Prevents broken models from affecting production. Enforces a "validate before commit" gate. |
| Compile-only `_VeloDBSqlClientStub` | MetricFlow needs a SqlClient for dialect rendering; real queries execute through the aiomysql pool under the user's identity. |
| Per-user connection pools | Each authenticated user gets an independent aiomysql pool, preserving VeloDB's native user-level authorization. |
| Inline HTML templates | The Web UI has no external CDN dependencies; single-file deployment; works behind proxies/VPNs. |
| Python 3.10 standalone build | Self-contained distribution via `python-build-standalone`. No system Python required at runtime. |
| Audit log (timed rotation) | Every Tool call records client_id, parameters, duration, success/failure. Rotated daily, retained 30 days. Sensitive data (cookies, passwords, tokens) is masked before hitting disk. |
| Web UI session affinity at the app layer | At login, the node IP is read from the request's local socket and written into the cookie; `SessionAffinityProxyMiddleware` forwards by cookie-suffix IP, keeping nginx a dumb proxy. |
| Async example deployment | Deployment can exceed a proxy's 60s idle timeout. Background task + status polling means the frontend never sees a 504 HTML page. |

---

## 12. Build and Distribution

### 12.1 Build commands (`build.sh`)

```bash
./build.sh linux-x64       # Linux x86_64
./build.sh linux-arm64     # Linux ARM64
./build.sh macos-x64       # macOS Intel
./build.sh macos-arm64     # macOS Apple Silicon
./build.sh                 # auto-detect the current platform
./build.sh clean           # remove python/, dist/, build artifacts
```

Downloads a standalone Python 3.10 distribution from `astral-sh/python-build-standalone`, installs dependencies per `requirements.txt`, and produces a self-contained full tar.gz package (server + client + docs + Python runtime) into `dist/`:

```
dist/
└── velodb-mcp-server-{version}-{platform}.tar.gz    ← python/ + src/ + config + mcp-client/
```

> The single source of truth for the version number is `pyproject.toml`; the `VERSION` environment variable of `build.sh` can override it (CI injects it from the Git tag).
> Note that `cryptography>=45.0.1` is a lower bound chosen for target-machine glibc 2.32 compatibility — do not raise it casually.

### 12.2 CI releases (`.github/workflows/release.yml`)

| Trigger | Behavior |
|---------|----------|
| Manually push a tag `velodb-mcp-server-x.y.z` | Build at the tag version and publish a Release |
| Manual trigger from Actions | Build at the input version and publish a Release |

Each release produces linux-x64 and linux-arm64 packages (private repos have no free ARM runners; arm64 is cross-built on an x64 runner via `build.sh`):

```
velodb-mcp-server-0.2.3-linux_x64.tar.gz
velodb-mcp-server-0.2.3-linux_arm64.tar.gz
```

Both packages extract to the same top-level directory `velodb-mcp-server/`, so deployment scripts need no changes.

### 12.3 Deployment

```bash
# 1. Extract
tar xzf velodb-mcp-server-{version}-linux_x64.tar.gz
cd velodb-mcp-server

# 2. Configure (optional; the default localhost:9030 works)
vim mcp-server.toml

# 3. Start
./start-mcp-server.sh                     # foreground
nohup ./start-mcp-server.sh > /tmp/velodb-mcp.log 2>&1 &   # background
```

No network, no pip, and no system Python are required at runtime. The bundled Python can be overridden via the `VELODB_MCP_PYTHON` environment variable:

```bash
VELODB_MCP_PYTHON=/usr/bin/python3.10 ./start-mcp-server.sh
```

### 12.4 Verification

```bash
# WebUI
curl http://<IP>:3000/mcp/web

# MCP agent integration
claude mcp add --transport http velodb http://<IP>:3000/mcp \
  --header "Authorization: Bearer admin:admin"
```

---

## 13. Directory Structure

```
velodb-mcp-server/
├── build.sh                     # build script
├── requirements.txt             # Python 3.10 dependencies
├── mcp-server.toml              # server configuration
├── start-mcp-server.sh          # startup script
├── mcp-client.sh                # client launcher script
├── INSTALL.html                 # installation guide
├── velodb-mcp-docs.html          # full documentation (semantic models + user guide)
├── DESIGN.md                    # this document
├── .github/workflows/
│   └── release.yml              # CI: auto-release on PR merge / tag release (x64 + arm64)
├── src/
│   ├── main.py                  # entry point + middleware stack + FastMCP.run()
│   ├── server.py                # service factory, 10 Tools, Web UI routes, REST API
│   ├── auth/                    # authentication module
│   │   ├── credential_cache.py  # 10-minute TTL in-memory cache
│   │   ├── credential_verifier.py # Bearer token → VeloDB verification
│   │   └── guard.py             # tool-level access control
│   ├── config/
│   │   └── loader.py            # TOML/YAML config + ${VAR} env interpolation
│   ├── core/                    # core modules
│   │   ├── connection.py        # aiomysql async connection pool
│   │   ├── pool_manager.py      # per-user pool factory
│   │   ├── audit.py             # timed-rotation audit log
│   │   ├── health.py            # service health tracking
│   │   ├── response.py          # JSON success/error responses
│   │   ├── sql_validator.py     # SQL read-only validation (sqlglot-based)
│   │   ├── charset.py           # charset middleware
│   │   ├── request_logger.py    # request logging middleware
│   │   ├── pagination.py        # cursor pagination
│   │   ├── sensitive_mask.py    # sensitive data masking
│   │   └── session_affinity_proxy.py # Web UI session-affinity ASGI reverse proxy (§8.3)
│   ├── store/                   # workspace storage module
│   │   ├── store.py             # VeloDBStore: per-workspace active/staging tables
│   │   ├── watcher.py           # MultiWorkspaceWatcher: poll, reload, validate, commit
│   │   ├── compiler.py          # MetricFlowCompiler + _VeloDBSqlClientStub
│   │   ├── manifest.py          # SemanticManifest: parse semantic_manifest.json
│   │   ├── bootstrap.py         # MetricFlow build (dbt parsing + manifest generation)
│   │   ├── seed.py              # example data seeding
│   │   └── version.py           # workspace version tracking
│   ├── tools/                   # Tool implementations
│   │   ├── dependency.py        # cross-file dependency detection (checked before safe YAML deletion)
│   │   ├── discovery.py         # list_databases, list_tables, describe_table
│   │   ├── query.py             # execute_query (SQL execution)
│   │   └── semantic.py          # list_metrics, list_dimensions_for_metric, query_metric
│   ├── skills/
│   │   └── velodb-mcp-skill.md   # query guide (returned by get_query_guide)
│   └── metricflow/              # bundled MetricFlow engine (compile-only mode)
└── mcp-client/                  # CLI client (separate package)
    └── client/
        ├── cli.py               # CLI entry point (typer framework)
        ├── config.py            # env/file configuration
        ├── http_client.py       # HTTP API client
        ├── mcp_client.py        # MCP streamable-http transport
        └── formatting.py        # output formatting
```
