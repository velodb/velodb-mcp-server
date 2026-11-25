"""Unified response format for all MCP tools."""

from __future__ import annotations

import json
from datetime import date, datetime
from decimal import Decimal
from enum import Enum
from typing import Any


class ErrorCode(str, Enum):
    READONLY_VIOLATION = "READONLY_VIOLATION"
    UNAUTHENTICATED = "UNAUTHENTICATED"
    PERMISSION_DENIED = "PERMISSION_DENIED"
    INVALID_SQL = "INVALID_SQL"
    QUERY_TIMEOUT = "QUERY_TIMEOUT"
    CONNECTION_ERROR = "CONNECTION_ERROR"
    VALIDATION_ERROR = "VALIDATION_ERROR"
    INVALID_PARAMS = "INVALID_PARAMS"
    METRIC_NOT_FOUND = "METRIC_NOT_FOUND"
    SERVICE_NOT_READY = "SERVICE_NOT_READY"
    SEMANTIC_DISABLED = "SEMANTIC_DISABLED"
    INTERNAL_ERROR = "INTERNAL_ERROR"


class _Encoder(json.JSONEncoder):
    """JSON encoder handling Doris data types."""

    def default(self, o: Any) -> Any:
        if isinstance(o, Decimal):
            return float(o)
        if isinstance(o, datetime):
            return o.isoformat()
        if isinstance(o, date):
            return o.isoformat()
        if isinstance(o, bytes):
            return o.decode("utf-8", errors="replace")
        return super().default(o)


def success_response(
    data: Any,
    meta: dict[str, Any] | None = None,
) -> str:
    """Build a success response JSON string."""
    resp: dict[str, Any] = {"success": True, "data": data}
    if meta:
        resp["meta"] = meta
    return json.dumps(resp, cls=_Encoder, ensure_ascii=False)


def error_response(
    code: ErrorCode,
    message: str,
    details: dict[str, Any] | None = None,
) -> str:
    """Build an error response JSON string."""
    err: dict[str, Any] = {"code": code.value, "message": message}
    if details:
        err["details"] = details
    return json.dumps({"success": False, "error": err}, cls=_Encoder, ensure_ascii=False)


# MySQL-protocol error codes Doris returns when the user is authenticated but
# lacks a privilege: database (1044), table (1142), column (1143) and
# operation-level (1227) access denials. 1045 is a failed login and stays a
# connection error.
_ACCESS_DENIED_CODES = frozenset({1044, 1142, 1143, 1227})
_LOGIN_FAILED_CODE = 1045


def is_access_denied_error(exc: BaseException) -> bool:
    """Return True when a Doris error means the caller lacks a privilege."""
    code = exc.args[0] if exc.args and isinstance(exc.args[0], int) else None
    if code == _LOGIN_FAILED_CODE:
        return False
    if code in _ACCESS_DENIED_CODES:
        return True
    # Doris also reports some privilege failures under generic codes (e.g. 1105):
    # "Access denied ..." or, from column/Ranger authorization,
    # "Permission denied: user [...] does not have privilege ...". A plain
    # "Permission denied" can be a storage or filesystem error, so it does not
    # count. A failed login says "using password" and is not a privilege
    # problem, even when it arrives wrapped without its 1045 code.
    message = str(exc).lower()
    if "using password" in message:
        return False
    return (
        "access denied" in message
        or "does not have privilege" in message
        or "permission denied: user" in message
    )


def db_error_response(exc: BaseException, permission_hint: str) -> str:
    """Build an error response for a failed Doris call.

    Privilege failures become PERMISSION_DENIED with *permission_hint* so the
    caller knows to request a grant instead of retrying. Anything else stays
    CONNECTION_ERROR.
    """
    if is_access_denied_error(exc):
        return error_response(
            ErrorCode.PERMISSION_DENIED,
            f"{permission_hint} Doris error: {exc}",
        )
    return error_response(ErrorCode.CONNECTION_ERROR, str(exc))
