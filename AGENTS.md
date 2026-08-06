# AGENTS.md — Agent Playbook for Installing velodb-mcp-server

> **Audience: AI agents (Claude Code, Cursor, Codex, etc.), not humans.**
> When a user asks you to install this MCP server and connect their VeloDB
> cluster, follow this playbook exactly. The user should never need to read
> any documentation — you do all the work; the user only answers a few
> questions.

## Trigger phrases

The user may say something like:

- "Install this MCP server per the README and tell me how to connect my VeloDB cluster."
- "Set up the VeloDB MCP for me."
- Any equivalent phrasing in any language (e.g. a Chinese request such as
  "install this MCP and tell me how to connect my VeloDB cluster")

If the request mentions installing/setting up this MCP server or connecting a
VeloDB cluster, you are in the right place. **Read this whole file first,
then execute the steps in order.**

## Overview of what you will do

```
Step 0  Ask the user 3 short questions (deployment type, connection info, install location)
Step 1  Detect the platform and download the matching release tarball
Step 2  Write the VeloDB FE address into mcp-server.toml
Step 3  Start the server in the background and health-check it
Step 4  Register the MCP server with the user's AI client (e.g. Claude Code)
Step 5  Verify end-to-end and report success
```

Do not skip steps. Verify each step before moving on. If a step fails,
consult the [Failure handling](#failure-handling) table.

---

## Step 0 — Collect information (ask once, then proceed)

Ask the user **exactly** these questions (in one message), then wait for the
answers:

1. **Deployment type**: VeloDB **Cloud** (managed service) or VeloDB
   **Enterprise** (self-hosted / on-premise)?
2. **Connection info**:
   - Cloud: the endpoint host (e.g. `abc123.ap-southeast-1.velodb.cloud`),
     username, and password.
   - Enterprise: the FE host (IP or hostname) and MySQL-protocol port
     (default `9030`), username, and password.
3. **Install location**: on **this machine** (where you are running), or on a
   **remote server**? If remote, ask how you can reach it (e.g. SSH access).

Rules:

- The VeloDB MySQL-protocol port is **9030** in both Cloud and Enterprise
  unless the user says otherwise. Do not ask about it separately; just confirm.
- The machine running this MCP server must be able to reach the FE host:9030.
  If the user picked Enterprise with a private IP and wants to install on a
  laptop that cannot reach that IP, tell them and ask for a reachable
  install location instead.
- Never print the user's password back in chat output beyond what is
  necessary to complete the setup commands.

---

## Step 1 — Detect the platform and download the release

The release tarballs are **fully self-contained** (they bundle a Python 3.10
runtime and all dependencies). **Never run `pip install` and never ask the
user to install Python.**

1. Detect the platform:

   ```bash
   uname -s && uname -m
   ```

   Map the result to a release asset suffix:

   | `uname -s` | `uname -m`        | Asset suffix  |
   |------------|-------------------|---------------|
   | Linux      | `x86_64`          | `linux_x64`   |
   | Linux      | `aarch64`/`arm64` | `linux_arm64` |
   | Darwin     | `arm64`           | `macos_arm64` |
   | Darwin     | `x86_64`          | `macos_x64`   |

2. Find the download URL. First work out the GitHub repository:
   - If you are inside a clone of this repo, run
     `git remote get-url origin` (or `git remote -v`) and derive
     `<owner>/<repo>` from it.
   - Otherwise ask the user for the repository URL.

3. Download the latest release asset matching the suffix, e.g.:

   ```bash
   # List the latest release assets and pick the matching tarball
   curl -fsSL https://api.github.com/repos/<owner>/<repo>/releases/latest \
     | grep -o "https://[^\"]*velodb-mcp-server-[^\"]*<suffix>\.tar\.gz" \
     | head -1
   ```

   ```bash
   curl -fsSL -o velodb-mcp-server.tar.gz "<asset-url>"
   mkdir -p ~/velodb-mcp-server
   tar xzf velodb-mcp-server.tar.gz -C ~/velodb-mcp-server --strip-components=1
   cd ~/velodb-mcp-server
   ```

   The tarball extracts to a top-level `velodb-mcp-server/` directory; using
   `--strip-components=1` into a dedicated folder keeps things tidy.

4. **Fallback — no matching release asset** (e.g. macOS has no CI build, or
   the release is missing): build from source instead. Clone the repo and run:

   ```bash
   ./build.sh <platform>   # linux-x64 | linux-arm64 | macos-x64 | macos-arm64
   tar xzf dist/velodb-mcp-server-*.tar.gz -C ~/velodb-mcp-server --strip-components=1
   ```

   The build downloads a standalone Python 3.10 automatically. If the network
   is restricted, it also accepts a local Python via
   `VELODB_MCP_SYSTEM_PYTHON=/path/to/python3.10` or a pre-downloaded tarball
   via `VELODB_MCP_PYTHON_TARBALL=/path/to/cpython-....tar.gz`.

---

## Step 2 — Configure the connection

Edit `mcp-server.toml` in the install directory. Only two keys matter:

```bash
cd ~/velodb-mcp-server
sed -i.bak \
  -e 's/^fe_host = .*/fe_host = "<FE_HOST>"/' \
  -e 's/^fe_port = .*/fe_port = <FE_PORT>/' \
  mcp-server.toml
```

- Cloud: `<FE_HOST>` is the endpoint host from Step 0, `<FE_PORT>` is `9030`.
- Enterprise: `<FE_HOST>` is the FE IP/hostname, `<FE_PORT>` is `9030`
  (unless the user gave a different one).

Leave everything else at defaults. Do not change `[query]` or `[logging]`
unless the user asks.

---

## Step 3 — Start the server

```bash
cd ~/velodb-mcp-server
nohup ./start-mcp-server.sh > /tmp/velodb-mcp-server.log 2>&1 &
sleep 3
curl -sf -o /dev/null -w "%{http_code}\n" http://127.0.0.1:3000/mcp/web/login
```

- Expected output: `200`. Anything else → see [Failure handling](#failure-handling).
- The server listens on port **3000** by default. If the user asked for a
  different port, set `mcp_port` in `mcp-server.toml` first and adjust all
  URLs below accordingly.

---

## Step 4 — Register the MCP server with the AI client

**Claude Code / Claude Desktop:**

```bash
claude mcp add --transport http velodb http://127.0.0.1:3000/mcp \
  --header "Authorization: Bearer <VELODB_USER>:<VELODB_PASSWORD>"
```

**Cursor / VS Code** — create or merge into the MCP config file
(`.cursor/mcp.json` or the editor's MCP settings):

```json
{
  "mcpServers": {
    "velodb": {
      "type": "http",
      "url": "http://127.0.0.1:3000/mcp",
      "headers": {
        "Authorization": "Bearer <VELODB_USER>:<VELODB_PASSWORD>"
      }
    }
  }
}
```

A ready-to-copy template ships in the install directory as
`mcp.json.example`.

Authentication is simply the **VeloDB username and password** joined with a
colon — every query runs under the caller's own VeloDB identity.

---

## Step 5 — Verify end-to-end

1. Call the health tool through the MCP endpoint:

   ```bash
   fastmcp call http://127.0.0.1:3000/mcp check_service_health \
     --auth "<VELODB_USER>:<VELODB_PASSWORD>" --json
   ```

   (If `fastmcp` is unavailable, skip to the next check.)

2. Or simply ask the user to start a **new** conversation and say:
   *"check the service health status"*. The agent in that conversation should
   report `"velodb": "connected"`.

3. Report success to the user, e.g.:

   > ✅ VeloDB MCP Server is installed and connected.
   > - Server: `http://127.0.0.1:3000/mcp` (Web UI at `/mcp/web`)
   > - Registered in Claude Code as `velodb`
   > - Connected to VeloDB at `<FE_HOST>:9030`
   > Try asking: "list all available metrics" or "show me the tables in my cluster".

Do **not** include the password in this summary.

---

## Failure handling

| Symptom | Likely cause | What you should do |
|---------|--------------|--------------------|
| `curl` to `/mcp/web/login` fails / empty | Server not up yet or crashed | `cat /tmp/velodb-mcp-server.log`, fix the reported error, restart |
| Port 3000 already in use | Another process holds it | `lsof -i :3000` (macOS) / `fuser -k 3000/tcp` (Linux), or set a different `mcp_port` |
| Health check returns 401 | Wrong username/password or bad token format | Token must be exactly `username:password`; re-run Step 4 with correct credentials |
| Health check shows `"velodb": "unavailable"` | FE unreachable from this machine | Verify `nc -zv <FE_HOST> 9030`; check `fe_host`/`fe_port` in `mcp-server.toml`; if Enterprise with a private IP, install on a machine inside that network |
| No release asset for this platform | CI builds Linux only | Use the source-build fallback in Step 1 |
| Workspace shows `not_ready` | Broken or missing semantic models | Log into the Web UI (`/mcp/web`) → Validate to see the error; unrelated to connectivity |

## Hard rules for agents

- Do not ask the user to read README/DESIGN/docs — **you** read them if you
  need details.
- Do not run `pip install` or require a system Python; the release is
  self-contained.
- Do not invent config keys. Only `fe_host`, `fe_port`, and (optionally)
  `mcp_port` should be touched.
- Do not expose the user's password in chat output or logs beyond the setup
  commands that need it.
- If anything is ambiguous (e.g. which FE to use when there are several),
  ask the user instead of guessing.
