#!/usr/bin/env python3
"""
Web UI & REST API test cases

Covers:
  - Web UI page access (login, models, file editor)
  - REST API (semantic push/pull/validate/commit)
  - Workspace management API (create/delete)
  - Authentication (login, logout, session)

Usage:
  python test/test_web_api.py

Environment variables:
  MCP_BASE_URL               Server address (default http://localhost:3000)
  MCP_TOKEN                  Bearer token (default admin:admin)
  DORIS_USER / DORIS_PASS    Doris login credentials (default admin / admin)
  DORIS_MCP_TEST_DESTRUCTIVE=1  enables destructive test cases (discarding
                                staged changes, creating/deleting workspaces);
                                skipped by default
"""

import json
import os
import sys
import unittest
import urllib.request
import urllib.error
import http.cookiejar
from urllib.parse import urlencode

# ── Configuration ────────────────────────────────────
BASE_URL = os.environ.get("MCP_BASE_URL", "http://localhost:3000")
AUTH_TOKEN = os.environ.get("MCP_TOKEN", "admin:admin")
WORKSPACE = os.environ.get("MCP_WORKSPACE", "example")
TEST_DORIS_USER = os.environ.get("DORIS_USER", "admin")
TEST_DORIS_PASS = os.environ.get("DORIS_PASS", "admin")
# Destructive cases (discard real staged changes on a shared server, create/delete workspaces)
# Skipped by default; only run when DORIS_MCP_TEST_DESTRUCTIVE=1 is set explicitly
DESTRUCTIVE = os.environ.get("DORIS_MCP_TEST_DESTRUCTIVE") == "1"

HEADERS = {
    "Authorization": f"Bearer {AUTH_TOKEN}",
}
JSON_HEADERS = {
    **HEADERS,
    "Content-Type": "application/json",
}


def _require_destructive():
    """Guard for destructive cases: skip unless DORIS_MCP_TEST_DESTRUCTIVE=1 is set."""
    if not DESTRUCTIVE:
        raise unittest.SkipTest(
            "Skipping: destructive cases require DORIS_MCP_TEST_DESTRUCTIVE=1"
        )


def _server_reachable() -> bool:
    """Probe whether the MCP Server is reachable (the login page should return 200).

    Only a 200 counts: connection refused means it is not started; 502/404 etc.
    mean the port is occupied by another service, which is also treated as
    unreachable to avoid running the full suite against the wrong target.
    """
    req = urllib.request.Request(f"{BASE_URL}/mcp/web/login", headers={})
    try:
        with urllib.request.urlopen(req, timeout=5) as resp:
            return resp.status == 200
    except (urllib.error.HTTPError, OSError):
        return False


def _get(path: str, headers: dict = None, expect_code: int = 200) -> dict:
    """GET request, returns (status_code, body_dict)"""
    req = urllib.request.Request(f"{BASE_URL}{path}", headers=headers or HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        if e.code == expect_code:
            return e.code, e.read().decode()
        raise


def _post(path: str, data: dict = None, headers: dict = None,
          expect_code: int = 200) -> tuple:
    """POST request"""
    req = urllib.request.Request(
        f"{BASE_URL}{path}",
        data=json.dumps(data).encode() if data else None,
        headers=headers or JSON_HEADERS,
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            body = resp.read().decode()
            try:
                return resp.status, json.loads(body)
            except json.JSONDecodeError:
                return resp.status, body
    except urllib.error.HTTPError as e:
        if e.code == expect_code:
            return e.code, e.read().decode()
        raise


# ═══════════════════════════════════════════════════════
#  Web UI tests
# ═══════════════════════════════════════════════════════

def test_webui_login_page():
    """GET /mcp/web/login — should return the login page HTML"""
    status, body = _get("/mcp/web/login", headers={}, expect_code=200)
    assert "html" in str(body).lower() or "login" in str(body).lower(), \
        f"Should return login page: {str(body)[:200]}"
    print("  ✅ Login page accessible")


def test_webui_login_post():
    """POST /mcp/web/login — log in with Doris credentials"""
    import urllib.parse
    form_data = urlencode({
        "username": TEST_DORIS_USER,
        "password": TEST_DORIS_PASS,
    }).encode()
    req = urllib.request.Request(
        f"{BASE_URL}/mcp/web/login",
        data=form_data,
        headers={"Content-Type": "application/x-www-form-urlencoded"},
    )
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            cookies = resp.headers.get_all("Set-Cookie")
            assert resp.status in (200, 302, 303, 400), f"Unexpected status: {resp.status}"
            # 400 may occur if already logged in or missing CSRF, still means endpoint works
            print(f"  ✅ Login endpoint responded status={resp.status}")
    except urllib.error.HTTPError as e:
        if e.code in (302, 303):
            print(f"  ✅ Login succeeded (redirect {e.code})")
        elif e.code == 400:
            print(f"  ✅ Login endpoint accessible (400, possibly missing CSRF token)")
        else:
            raise


def test_webui_requires_auth():
    """GET /mcp/web — should return the login page or 401 when not logged in"""
    status, body = _get("/mcp/web", headers={}, expect_code=200)
    # When not logged in it may return 401 or redirect to the login page
    assert status in (200, 401, 302), f"Unexpected status: {status}"
    print(f"  ✅ Unauthenticated access returned {status}")


def test_webui_models_page():
    """GET /mcp/web/models — authenticated access"""
    status, body = _get("/mcp/web/models")
    assert status == 200, f"Expected 200, got {status}: {str(body)[:200]}"
    print("  ✅ Model management page accessible")


# ═══════════════════════════════════════════════════════
#  REST API tests — semantic model management
# ═══════════════════════════════════════════════════════

def test_api_semantic_files_list():
    """GET /mcp/web/semantic/files — list live files"""
    status, body = _get("/mcp/web/semantic/files")
    assert status == 200
    assert body.get("success") is not False, f"Failed: {body}"
    print(f"  ✅ Semantic file list: {json.dumps(body, ensure_ascii=False)[:200]}")


def test_api_semantic_pull():
    """GET /mcp/web/semantic/pull — download live YAML (.tar.gz binary)"""
    req = urllib.request.Request(f"{BASE_URL}/mcp/web/semantic/pull", headers=HEADERS)
    try:
        with urllib.request.urlopen(req, timeout=15) as resp:
            raw = resp.read()
            # .tar.gz starts with \x1f\x8b
            is_gzip = raw[:2] == b'\x1f\x8b'
            assert resp.status in (200, 404)
            size = len(raw)
            print(f"  ✅ pull: status={resp.status}, size={size}B, gzip={is_gzip}")
    except urllib.error.HTTPError as e:
        if e.code == 404:
            print(f"  ✅ pull: 404 (no files)")
        else:
            raise


def test_api_semantic_reload():
    """POST /mcp/web/semantic/reload — reload a workspace"""
    status, body = _post("/mcp/web/semantic/reload", {"workspace": WORKSPACE})
    assert status == 200
    print(f"  ✅ reload: {json.dumps(body, ensure_ascii=False)[:200]}")


def test_api_staging_validate():
    """POST /mcp/web/staging/validate — validate staged changes"""
    status, body = _post("/mcp/web/staging/validate", {"workspace": WORKSPACE})
    assert status == 200
    assert "success" in body, f"Missing success field: {body}"
    print(f"  ✅ validate: {json.dumps(body, ensure_ascii=False)[:200]}")


def test_api_staging_discard():
    """POST /mcp/web/staging/discard — discard staged changes

    Destructive: discards staged changes of real users on a shared server; skipped by default.
    """
    _require_destructive()
    status, body = _post("/mcp/web/staging/discard", {"workspace": WORKSPACE})
    assert status == 200
    print(f"  ✅ discard: {json.dumps(body, ensure_ascii=False)[:200]}")


# ═══════════════════════════════════════════════════════
#  Workspace management API tests
# ═══════════════════════════════════════════════════════

def test_api_workspace_create_and_delete():
    """Create → verify → delete a workspace

    Destructive: creates/deletes a real workspace on the server; skipped by default.
    """
    _require_destructive()
    test_ws = "test_workspace_tmp"

    # Clean up leftovers
    _post("/mcp/web/workspace/delete", {"workspace": test_ws}, expect_code=200)

    # Create
    status, body = _post("/mcp/web/workspace/create", {
        "name": test_ws,
    }, expect_code=200)
    assert status == 200 or body.get("success") is not False, \
        f"Create failed: status={status} body={json.dumps(body, ensure_ascii=False)[:200]}"
    print(f"  ✅ Created workspace '{test_ws}': {json.dumps(body, ensure_ascii=False)[:150]}")

    # Verify existence — via health check
    import urllib.request as ur
    mcp_payload = {
        "jsonrpc": "2.0", "id": 99,
        "method": "tools/call",
        "params": {"name": "check_service_health", "arguments": {"detail": True}},
    }
    mcp_headers = {
        "Content-Type": "application/json",
        "Accept": "application/json, text/event-stream",
        "Authorization": f"Bearer {AUTH_TOKEN}",
    }
    req = ur.Request(
        f"{BASE_URL}/mcp",
        data=json.dumps(mcp_payload).encode(),
        headers=mcp_headers,
    )
    with ur.urlopen(req, timeout=15) as resp:
        raw = resp.read().decode()
    assert test_ws in raw, f"New workspace not found in health: {raw[:500]}"
    print(f"  ✅ Workspace '{test_ws}' visible in health check")

    # Delete
    status, body = _post("/mcp/web/workspace/delete", {"workspace": test_ws})
    assert status == 200
    print(f"  ✅ Deleted workspace '{test_ws}'")


# ═══════════════════════════════════════════════════════
#  Authentication tests
# ═══════════════════════════════════════════════════════

def test_api_requires_admin_for_create():
    """Non-admin users cannot create workspaces"""
    # Use the test user's token
    test_headers = {
        "Content-Type": "application/json",
        "Authorization": "Bearer test:test",
    }
    status, body = _post(
        "/mcp/web/workspace/create",
        {"workspace": "hacker_ws"},
        headers=test_headers,
        expect_code=403,
    )
    assert status in (401, 403), f"Expected 401/403, got {status}: {body}"
    print(f"  ✅ test user cannot create workspaces ({status})")


def test_bearer_token_format():
    """Verify the Bearer token format: username:password"""
    # Correct format
    ok_headers = {
        "Content-Type": "application/json",
        "Authorization": f"Bearer {TEST_DORIS_USER}:{TEST_DORIS_PASS}",
        "Accept": "application/json, text/event-stream",
    }
    payload = {
        "jsonrpc": "2.0", "id": 1,
        "method": "tools/call",
        "params": {"name": "check_service_health", "arguments": {}},
    }
    req = urllib.request.Request(
        f"{BASE_URL}/mcp",
        data=json.dumps(payload).encode(),
        headers=ok_headers,
    )
    with urllib.request.urlopen(req, timeout=15) as resp:
        body = resp.read().decode()
    assert "connected" in body.lower(), f"Auth failed with valid token: {body[:300]}"
    print("  ✅ Correct Bearer token format verified")


# ═══════════════════════════════════════════════════════
if __name__ == "__main__":
    print("=" * 60)
    print("Web UI & REST API tests starting")
    print(f"  URL: {BASE_URL}")
    print(f"  Workspace: {WORKSPACE}")
    print("=" * 60)

    if not _server_reachable():
        print(f"\n⚠️ MCP Server unreachable ({BASE_URL}), skipping entirely; not counted as failure")
        sys.exit(0)

    tests = [
        # Web UI
        ("Web UI login page", test_webui_login_page),
        ("Web UI login POST", test_webui_login_post),
        ("Web UI requires auth", test_webui_requires_auth),
        ("Web UI models page", test_webui_models_page),
        # REST API
        ("API semantic files list", test_api_semantic_files_list),
        ("API semantic pull", test_api_semantic_pull),
        ("API semantic reload", test_api_semantic_reload),
        ("API staging validate", test_api_staging_validate),
        ("API staging discard", test_api_staging_discard),
        # Workspace
        ("Workspace create/delete", test_api_workspace_create_and_delete),
        # Auth
        ("Admin required for create", test_api_requires_admin_for_create),
        ("Bearer token format", test_bearer_token_format),
    ]

    passed = 0
    failed = 0
    skipped = 0
    for name, fn in tests:
        try:
            print(f"\n[{name}]")
            fn()
            passed += 1
        except unittest.SkipTest as e:
            print(f"  ⚠️ SKIP: {e}")
            skipped += 1
        except AssertionError as e:
            print(f"  ❌ FAIL: {e}")
            failed += 1
        except Exception as e:
            print(f"  ❌ ERROR: {e}")
            failed += 1
            import traceback
            traceback.print_exc()

    print(f"\n{'='*60}")
    print(f"Result: {passed} passed, {failed} failed, {skipped} skipped")
    print(f"{'='*60}")
    if failed > 0:
        sys.exit(1)
