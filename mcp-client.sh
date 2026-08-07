#!/usr/bin/env bash
SCRIPT_DIR="$(cd "$(dirname "$0")" && pwd)"
PYTHON="${VELODB_MCP_PYTHON:-$SCRIPT_DIR/python/bin/python3}"
export PYTHONPATH="$SCRIPT_DIR/mcp-client"
exec "$PYTHON" -m client.cli "$@"
