# Test Cases — velodb-mcp-server

Generated from [DESIGN.md](../DESIGN.md), [INSTALL.html](../INSTALL.html), and [velodb-mcp-docs.html](../velodb-mcp-docs.html).

## File Overview

### Offline unit tests (no MCP Server / VeloDB required)

| File | Contents |
|------|----------|
| `test_sql_validator.py` | `core.sql_validator.validate_readonly` read-only SQL validation (allow/block/multi-statement/comment bypass/known prefix behavior) |
| `test_sensitive_mask.py` | `core.sensitive_mask` password/token masking |
| `test_pagination.py` | `core.pagination` pagination and token TTL behavior |
| `test_private_ip_config.py` | Request node IP resolution and startup wiring |
| `test_deps.py` | Runtime dependency guard (imports real modules) |
| `test_cross_file_deps.py` | Cross-file dependency detection before deletion |
| `test_credential_pass.py` | Request-level credential pass-through to the store layer |
| `test_watcher.py` | `MultiWorkspaceWatcher.ensure_fresh` cooldown/reload/degradation |
| `test_web_session_cookie.py` | Web session cookie |
| `test_session_affinity_proxy_routing.py` | Session-affinity proxy routing |
| `test_session_affinity_proxy_streaming.py` | Session-affinity proxy streaming forwarding |
| `test_session_affinity_proxy_relogin.py` | Session-affinity proxy re-login |
| `test_session_affinity_proxy_force_target.py` | Session-affinity proxy request-address routing |

### Online tests (require a running MCP Server + VeloDB)

| File | Contents |
|------|----------|
| `test_mcp_tools.py` | All 10 MCP Tools + authentication + E2E (30 cases) |
| `test_web_api.py` | Web UI + REST API + workspace management (12 cases) |

### Entry script

| File | Contents |
|------|----------|
| `run_all_tests.sh` | One-command runner supporting `--offline` / `--tools` / `--web` / `--smoke` |

## Requirements

| Requirement | Notes |
|-------------|-------|
| MCP Server | Running at `localhost:3000` (online tests only) |
| VeloDB FE | Running at `127.0.0.1:9030` (online tests only) |
| Authentication | `admin:admin` |
| Python | 3.10+; offline tests use the project `.venv` (`PYTHONPATH=src`) |

Defaults can be overridden via environment variables:
```bash
export MCP_URL=http://192.168.1.100:3000/mcp
export MCP_BASE_URL=http://192.168.1.100:3000
export MCP_TOKEN=admin:admin
export MCP_WORKSPACE=example
export VELODB_USER=admin          # test_web_api.py login credentials
export VELODB_PASS=admin
export VELODB_MCP_TEST_DESTRUCTIVE=1  # enable destructive cases (see below)
```

## How to Run

```bash
# Offline unit tests only (no services need to be started)
bash test/run_all_tests.sh --offline

# Or run all offline cases via unittest discover (online files collect no cases; harmless)
PYTHONPATH=src .venv/bin/python -m unittest discover -s test -p 'test_*.py'

# Individual offline files can also run directly (built-in sys.path bootstrap)
.venv/bin/python test/test_watcher.py

# Full suite (requires the MCP Server online)
bash test/run_all_tests.sh

# Smoke test only (fast, ~5s)
bash test/run_all_tests.sh --smoke

# MCP Tool tests only
bash test/run_all_tests.sh --tools

# Web/API tests only
bash test/run_all_tests.sh --web

# Or run with Python directly (skipped entirely with exit code 0 when the server is unreachable)
python test/test_mcp_tools.py
python test/test_web_api.py
```

## Destructive Cases

The following cases in `test_web_api.py` affect shared server state and are
**skipped by default**; they only run when `VELODB_MCP_TEST_DESTRUCTIVE=1` is
set explicitly:

- `test_api_staging_discard` — discards real users' staged changes
- `test_api_workspace_create_and_delete` — creates/deletes a real workspace

## Test Coverage Matrix

### MCP Tool tests (10 Tools)

| Tool | Scenarios | Assertions |
|------|-----------|------------|
| `get_query_guide` | Get the workflow guide | Returned text >100 chars, contains keywords |
| `check_service_health` | Basic/detailed | VeloDB=connected, workspaces present |
| `list_metrics` | List/pagination | data array, meta.total_count |
| `list_dimensions_for_metric` | Dimensions by metric | data contains dimensions |
| `query_metric` | Basic/group_by/where/order+limit | 4 query modes |
| `list_databases` | List/pagination | dw,mysql,system_mcp,information_schema |
| `list_tables` | mysql db/dw seed tables/like fuzzy match | 4 seed tables verified |
| `describe_table` | summary/full/names | 3 detail levels |
| `execute_query` | SELECT/VERSION/SHOW/EXPLAIN/max_rows/write blocking | 7 scenarios |
| `reload_semantic_layer` | Manual reload | Returns structured JSON with a success field |

Note: semantic-layer cases (`list_dimensions_for_metric`, the `query_metric`
series) are skipped only when the MCP Server is unreachable (connection-type
errors); assertion failures such as the semantic layer not being ready are
honestly counted as FAIL and are no longer silently skipped.

### Web UI & API tests

| Category | Test points |
|----------|-------------|
| **Web UI** | Login page, login submit, unauthenticated blocking, model management page |
| **REST API** | Semantic file list, pull download, reload, staging validate/discard |
| **Workspace** | Create → verify existence → delete (full lifecycle; destructive; skipped by default) |
| **Authentication** | admin permission control, Bearer token format validation |

### Boundary & error tests

| Scenario | Expected |
|----------|----------|
| No Authorization | 401/403 |
| Invalid token | 401/403 |
| Write SQL (INSERT) | Blocked |
| SQL syntax error | Friendly error message |
| Non-admin creating a workspace | 403 |
| Nonexistent metric | Friendly error |

### End-to-end test (Agent workflow)

```
get_query_guide → check_service_health → list_databases
  → list_tables → describe_table → execute_query
```
