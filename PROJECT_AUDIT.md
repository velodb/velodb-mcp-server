# doris-new-mcp Project Audit Report

> Audit method: read-only static review (5 parallel review tracks: core code quality / security / config-build-doc consistency / test quality / vendored MetricFlow). No files were modified.
> Audit date: 2026-07-30

## Overall Assessment

The project's core idea (MCP + semantic layer + per-user connection pools) is clear, and some newer modules (session-affinity proxy, watcher, offline unit tests) are of high quality. However, the project is currently in a state of **rapidly piling on features without engineering consolidation**: there are 2 confirmed credential leakage points, 1 SQL injection exploitable by any authenticated user, 1 operation that silently amplifies cluster-wide privileges, and a large number of dead features that "look present but aren't" (auth, db_whitelist, tool authorization). **Deployment to public-network / multi-tenant environments is not recommended in the current state.**

---

## 1. Critical Issues (fix first)

### 1. Plaintext passwords written to log files (security-incident level)
- `src/core/request_logger.py:33-36, 70-83`: the middleware logs all request headers (including `Authorization: Bearer username:password` and `Cookie`) and request bodies (including the WebUI login form's `password=xxx`) at INFO level, and the logger is forced to DEBUG.
- `src/main.py:38-39` mounts it unconditionally; logs persist to `workspace/logs/server.log`.
- The project already has `src/core/sensitive_mask.py`, but it is not wired into this middleware at all.
- **Fix**: wire sensitive_mask into the middleware and hard-filter the `Authorization`/`Cookie`/`password` fields; or disable request body/header logging by default.

### 2. SQL injection via the workspace query parameter (any authenticated user)
- `src/server.py:327-329` `_get_workspace_from_request` does not validate the `?workspace=` value; it flows into `DorisStore(workspace=ws)` (`src/store/store.py:103-107`) and is interpolated via f-strings into `CREATE/DROP/USE/SELECT` SQL (e.g. `store.py:164, 476-496`) and file paths (`src/store/watcher.py:277`).
- Non-admin routes also trigger it (`/mcp/web/models`, `api_semantic_pull`, `api_staging_list`, etc.); the `_VALID_WORKSPACE_NAME` regex is only checked in the create endpoint (`server.py:953`).
- **Fix**: apply `_VALID_WORKSPACE_NAME` validation uniformly in `_get_workspace_from_request` (allow-list character set, single choke point).

### 3. Deploying the example silently executes a global GRANT
- `src/server.py:198-204`: after the admin deploys the example, it executes `GRANT SELECT_PRIV ON *.* TO '%'` — i.e. **read access to every database and table in the cluster for all users**, far beyond the intent of "make the example queryable"; failure only logs a warning, with no confirmation and no documentation warning.
- **Fix**: grant privileges only on the example database/tables; add explicit confirmation and prominent documentation.

### 4. Authentication/authorization layer largely non-functional ("looks secure, isn't")
- `src/auth/guard.py:33-36` `check_tool_access` always allows; any valid Doris user can call admin tools such as `reload_semantic_layer`; the audit log's `client_id` is always `None`.
- `src/config/loader.py:106` `self.auth = None` — the `[auth]` config section is never parsed; the entire static/JWT/OAuth stack (`src/auth/provider.py`, 188 lines + `config.py`, 482 lines) is dead code; an operator who configures JWT would wrongly believe it is in effect.
- `src/config/loader.py:78` `db_whitelist` is hard-coded to empty, so the allow-list feature is entirely ineffective.
- **Fix**: pick one — either wire it up (parse the config, actually validate scopes/allow-list), or delete it and state clearly in the docs that "Doris credentials are the only authentication".

### 5. Reflected XSS (multiple WebUI locations)
- `src/server.py:1227-1229` (`?staged=` flash message), `server.py:1787, 1791-1792` (`filename` path parameter inserted into HTML and form action attributes), `server.py:1154` (login failure echoes `user`), `server.py:1540` (exception message containing a filename).
- `_html_escape` (`server.py:1455`) already exists in the file but is only applied in some places.
- **Fix**: pass all user-controllable input through `_html_escape` before inserting into HTML; quote and escape attribute contexts.

### 6. Event loop stalled by synchronous blocking IO
- Many HTTP routes directly call blocking pymysql methods synchronously: `server.py:1231-1232` (list_files), `server.py:1655, 1670` (validate/commit_staging, which include seconds-long MetricFlow compilation), `_get_store` connects at construction time (`store.py:107, 469`), `server.py:1560, 1585, 1628`, etc.
- Similar calls elsewhere do use `asyncio.to_thread` (`server.py:843, 846`), indicating inconsistent style rather than intent.
- **Fix**: wrap all pymysql/compilation calls in `asyncio.to_thread`.

### 7. Connection pool has no liveness probing; cancelled connections are returned to the pool polluted
- `src/core/connection.py:43-52`: the aiomysql pool has no `pool_recycle`/ping; the `pool_idle_timeout` config (`loader.py:75`) is read but never used. After Doris `wait_timeout` drops idle connections, the first query always errors (a classic intermittent production failure).
- `src/core/connection.py:71`: after `asyncio.wait_for` cancels `cur.execute`, the connection's protocol state is unknown yet it is returned to the pool normally; subsequent reuse may read misaligned protocol packets.
- **Fix**: enable `pool_recycle` (or ping on acquisition); discard connections on the cancel/exception path instead of returning them.

### 8. Watcher reload concurrency race
- `src/store/watcher.py:337-362`: `ws.parsing` read-modify-write has no lock, while `ensure_fresh` runs concurrently across threads via `asyncio.to_thread` (`server.py:706, 766, 783, 810`); concurrent fetch+compile+atomic swap on the same workspace can corrupt manifest state; `MetricRouter.rebuild` (`watcher.py:166-179`) has a window between clear and rebuild during which `resolve` reads an empty table.
- **Fix**: one `threading.Lock` per workspace; rebuild should build-then-swap (double buffering).

### 9. The packaged CLI client always crashes: missing `typer` dependency
- From `mcp-client/client/cli.py:9` the entire CLI uses typer, but typer is not in requirements.txt / pyproject.toml / uv.lock / the release tarball (verified: 0 typer files inside the tar.gz). Running `./mcp-client.sh` immediately raises `ModuleNotFoundError`.
- Related doc drift: `DESIGN.md:604` claims the CLI uses cyclopts; it actually uses typer.
- **Fix**: add `typer` to requirements.txt; fix DESIGN.md.

### 10. License and dependency compliance issues in vendored MetricFlow
- **Apache-2.0 compliance gaps**: `src/metricflow/` (three upstream v0.209.0 packages merged and rewritten) has no LICENSE/NOTICE/copyright headers; modifications were made (`sql/render/doris.py`, `msi_pydantic_shim.py`, etc.) without the notices required by §4(b); the dist tarball is effectively redistributing this code.
- **Runtime dependencies by luck**: `more_itertools`, `jsonschema`, `referencing` are undeclared (pulled in transitively); `graphviz` is entirely missing (currently dead code — it crashes as soon as it is imported); `pydantic>=2.13` has no upper bound, while the vendored code uses the `pydantic.v1` compatibility layer throughout, so pydantic v3 will break the whole tree.
- **Fix**: add LICENSE/NOTICE and modification notices; declare `more-itertools/jsonschema/typing_extensions` directly in pyproject, cap pydantic with `<3`; delete dead code such as the graphviz visualization.

---

## 2. Major Issues

### Functional bugs (confirmed)
1. **Dependency checks entirely ineffective for model references**: `src/tools/dependency.py:134` `refs["models"]` is never populated, so deleting a referenced semantic model is not blocked (dead path).
2. **`resolve_where` corrupts string literals**: `src/store/compiler.py:302-305` — the regex blindly replaces without distinguishing column references from literals, so `channel = 'channel'` produces wrong SQL; `having/order_by` (:449-458) are f-stringed directly into SQL.
3. **`list_dimensions_for_metric` returns empty dimensions for ratio/conversion/multi-level derived metrics**: `src/store/manifest.py:111-129` only recognizes measures and one level of metrics, misleading the LLM.
4. **`query_metric`'s having/where is a raw SQL injection channel**: `compiler.py:253-255, 329` concatenates user input, and `src/tools/semantic.py:79` executes it directly without `validate_readonly` — "read-only validation" is meaningless on the metric path.
5. **Local patch bug**: `src/metricflow/.../project_configuration.py:48` falls back to `version("mcp-server")`, but the dist name is `doris-new-mcp`, so `dsi_package_version` is always `0.0.0` and gets written into the manifest.

### Security / sessions
6. **WebUI sessions store Doris passwords in plaintext**: `server.py:1159-1165` — an in-memory dict stores plaintext passwords with a 24h TTL and no capacity limit; the cookie (:1180-1184) lacks the `Secure` flag; the default `mcp_host=0.0.0.0` serves plaintext HTTP.
7. **No brute-force protection**: `/mcp/web/login` and Bearer validation have no rate limiting; CredentialCache only caches successes, so every failure hits Doris = an online password-guessing channel.
8. **Hard-coded `user == "admin"` as the sole administrator check** (`server.py:416`), decoupled from Doris's actual privilege system.
9. **Audit masking too narrow**: `sensitive_mask.py:7-12` only handles the `password=` and `sk-` patterns; `execute_query` writes `sql[:200]` (including business-data literals) into the audit log.
10. **Session-affinity proxy forwards in plaintext on the internal network**: `session_affinity_proxy.py:223` `http://{target_ip}` forwards request bodies containing passwords, relying on the trusted-internal-network assumption.

### Engineering quality
11. **`create_server` is an 1800-line god function** (`src/server.py:124-1799`): 12+ MCP tools, 20+ routes, ~400 lines of HTML templates all in one function, 10+ nonlocal closure states; the dependency-check sections of `api_semantic_delete_file` and `semantic_webui_delete` are verbatim duplicates.
12. **Batches of dead code**: `semantic_guard.py` (whole module), `state.py` (whole module), `tools/query.py:43-63` execute_sql, `sql_validator.py:79-99` validate_write, the entire `auth/provider.py` + `auth/config.py`, `guard.check_tool_access`, `PoolManager.get_pool/cleanup_idle_pools`, multiple public methods on manifest/router/watcher — all with zero callers by grep.
13. **`ensure_fresh` has no negative caching**: `watcher.py:321-331` runs a full `SHOW TABLES` every time for a nonexistent workspace, creating steady probing overhead when the LLM uses a wrong name.
14. **Config surface doesn't match reality**: `ClusterConfig`'s `fe_host/user_name/user_password` are hard-coded (`loader.py:69-72`) and the corresponding config keys are ignored; `pool_idle_timeout` is read but unused.
15. **Startup overly sensitive to network**: `main.py:18` → `server.py:89-98` sends a UDP probe to 8.8.8.8 to determine the local IP and requires it to be an RFC-1918 private address; public servers / pure IPv6 / offline environments crash at startup.

### Version / build / documentation chaos
16. **Version numbers contradict each other in five places**: pyproject=0.1.0, build.sh default=0.3.0, README/DESIGN=0.3.0, INSTALL.html=0.2.0, actual dist package=1.3.1, release.yml derives from the tag. No single source of truth.
17. **Dual-track dependencies have diverged**: conflicting `cryptography` lower bounds (pyproject ≥48 vs requirements ≥45); direct runtime dependencies `httpx` (session_affinity_proxy.py:11) and `importlib_metadata` are missing from pyproject; `python-dateutil` is duplicated in requirements.txt.
18. **README deployment instructions are wrong**: `README.md:73-74` tells users to `cd doris-new-mcp`, but the tarball's top-level directory is `doris-mcp-server/`.
19. **README/DESIGN still say "two tar.gz files"**, while build.sh switched to a single package long ago; the Python version is inconsistent in three places (build.sh pins 3.10.16 / `.python-version` says 3.13 / pyproject only says >=3.10).
20. **pyproject.toml placeholder**: `description = "Add your description here"`; no package-data declaration, and `src/skills/doris-mcp-skill.md` is read via `importlib.resources` (`server.py:541`) — if packaging omits it, `get_query_guide` fails silently.

### Tests
21. **`test/test_grant.py` is a fake test**: the entire file tests functions defined within itself and never touches src code (the real GRANT logic is inlined in server.py with a different signature). All 6 cases pass with zero assurance.
22. **The security-critical `sql_validator.py` has zero unit tests** (99 lines of pure functions, trivially testable); 19 modules including `pool_manager/sensitive_mask/audit/request_logger/auth/*` have no tests at all.
23. **5 semantic-layer tests in `test_mcp_tools.py` have meaningless assertions**: `except Exception: skip` swallows everything including AssertionError; several assertions are tautologies (`"result" in result or "error" in result`).
24. **No unified test entry point**: `run_all_tests.sh` runs only 7 of 13 files; 6 files are never executed; CI (release.yml) has no test step; 3 files lack sys.path bootstrapping and cannot run standalone; the README's "42 cases" figure is stale (137 can actually be collected offline).
25. **Integration tests have real side effects**: `test_web_api.py` creates/deletes workspaces on a real server and discards staged changes of real users on a shared server; hard-coded `admin:admin` + `127.0.0.1:9030` with no skip mechanism.

---

## 3. Minor Suggestions (selected)

- `connection.py:64-65` `timeout or ...` silently replaces an explicit 0; `pagination.py:34-43` silently falls back to page 1 for an expired page_token, so the LLM gets duplicate data without knowing.
- `tools/query.py:37` relies on the Python 3.11+ `TimeoutError` alias, conflicting with the `requires-python = ">=3.10"` declaration (wrong timeout error type on 3.10).
- `server.py:826` uses the substring check `'"success": true' in result` to judge tool results; data containing this substring causes false positives.
- `store.py:469-501` `_ensure_tables` swallows exceptions with only a warning, so errors surface far from the root cause and CREATE TABLE is re-issued every time; `store.py:189-190` `except Exception: pass` degrades the revision to `""`, triggering repeated watcher reloads.
- `sql_validator.py:36-41` passes statements through by prefix when parsing fails, and `EXPLAIN` has no word-boundary check; :69-73 still does raw-text prefix pass-through after successful parsing, widening the pass-through surface (currently limited risk since execute_sql is not registered).
- `.gitignore` lacks `.env`, `mcp-client.toml`, `*.pem/*.key`; has duplicate lines; `test_demo/` and `test_demo.zip` (containing `__MACOSX` junk) — pick one.
- Local `.mcp.json:7` contains a plaintext `Bearer admin:test_123` pointing to a real VeloDB Cloud instance (gitignored, so it won't be committed, but the credential itself is weak — recommend rotating it).
- `credential_cache.py:35` cache key is unsalted SHA256(username:password); after a password change, the old password remains valid for up to 10 minutes.
- `get_machine_ip` ties authentication to the result of an 8.8.8.8 UDP probe — an odd availability/security coupling.
- build.sh mode 2 does not validate the `DORIS_MCP_SYSTEM_PYTHON` version, while the slimming path hard-codes `python3.10`; pointing to 3.11/3.13 silently breaks.
- Root `mcp-client.sh` and `mcp-client/mcp-client.sh` are byte-for-byte duplicates maintained in two places.
- `version.py:64-73` `touch_epoch` modifies a field it calls an "Immutable snapshot" — the comment contradicts the behavior.
- `discovery.py:51,74,98,109` LIKE/table-name f-string concatenation (inconsistent with the parameterized style in store).
- The vendored MetricFlow tree contains telemetry, 8 unused data-warehouse renderers, and other redundant dead code, widening the attack/dependency surface.

---

## 4. Recommendations (by priority)

**P0 — Stop the security bleeding (1-2 days)**
1. Mask request_logger: filter `Authorization`/`Cookie`/`password`; don't log request bodies by default.
2. Add `_VALID_WORKSPACE_NAME` allow-list validation uniformly in `_get_workspace_from_request` (one-line choke point, closes the main injection entry).
3. Remove/narrow `GRANT SELECT_PRIV ON *.* TO '%'`; grant only on the example tables.
4. Pass all WebUI output through `_html_escape`; add `Secure` to cookies (or mandate TLS at the reverse proxy in docs).
5. Delete or wire up dead security features: the auth config section, `check_tool_access`, `db_whitelist` — the worst state is "looks like it's there".
6. Add `typer` to requirements.txt (otherwise the released client is broken); add `httpx`, `importlib-metadata`, `more-itertools`, `jsonschema` to pyproject; cap pydantic with `<3`.

**P1 — Correctness and stability (1 week)**
7. Wrap all pymysql/compilation calls in `asyncio.to_thread`; add `pool_recycle` to the connection pool; discard connections on the cancel path.
8. Add a per-workspace lock in the watcher + double-buffered router rebuild.
9. Fix the 4 confirmed functional bugs: dependency models dead path, resolve_where literal corruption, ratio/conversion dimensions, read-only validation on the metric path.
10. Add LICENSE/NOTICE to `src/metricflow/`, record the 4 local patches (or switch to a standard vendor process), delete graphviz dead code.

**P2 — Engineering consolidation (ongoing)**
11. Single source of truth for the version number (recommend build.sh reads pyproject); fix README `cd doris-mcp-server`; sync README/DESIGN with the single-package build and 4 platforms.
12. Split `create_server`: separate WebUI (HTML/routes) from MCP tools into different files; delete the batches of dead code (semantic_guard, state, execute_sql, auth provider — or wire them up).
13. Tests: delete/rewrite the fake test_grant.py; add pure offline unit tests for sql_validator, pool_manager, sensitive_mask; fix sys.path bootstrapping; include all offline files in run_all_tests.sh; add an offline test step to CI; add environment-detection skips to integration tests.
14. Align config with behavior: make ClusterConfig's hard-coded fields read from config or remove the config keys; implement or delete `pool_idle_timeout`.

---

*This report is based on a static, read-only review and did not connect to a real Doris instance to verify runtime behavior; entries marked "suspected" should be reproduced and confirmed before fixing.*
