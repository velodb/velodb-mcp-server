"""Offline tests: Doris privilege failures surface as PERMISSION_DENIED (#11)."""

import asyncio
import json
import sys
import unittest
from pathlib import Path
from unittest.mock import MagicMock

import pymysql

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from core.response import is_access_denied_error  # noqa: E402
from tools.discovery import describe_table, list_databases, list_tables  # noqa: E402
from tools.query import execute_query  # noqa: E402
from tools.semantic import query_metric  # noqa: E402

_DB_DENIED = pymysql.err.OperationalError(
    1044, "Access denied for user 'alice'@'%' to database 'dw'"
)
_TABLE_DENIED = pymysql.err.OperationalError(
    1142, "SELECT command denied to user 'alice'@'%' for table 'orders'"
)
_LOGIN_FAILED = pymysql.err.OperationalError(
    1045, "Access denied for user 'alice'@'10.0.0.5' (using password: YES)"
)
_UNREACHABLE = pymysql.err.OperationalError(
    2003, "Can't connect to MySQL server on '10.0.0.9'"
)


class _FailingPool:
    def __init__(self, exc: Exception) -> None:
        self._exc = exc

    async def execute(self, *args, **kwargs):
        raise self._exc


def _error(result: str) -> dict:
    body = json.loads(result)
    assert body["success"] is False
    return body["error"]


def _run(coro):
    return asyncio.run(coro)


class AccessDeniedDetectionTests(unittest.TestCase):
    def test_privilege_error_codes_are_access_denied(self) -> None:
        for code in (1044, 1142, 1143, 1227):
            self.assertTrue(is_access_denied_error(pymysql.err.OperationalError(code, "denied")))

    def test_generic_code_with_access_denied_message_is_access_denied(self) -> None:
        exc = pymysql.err.OperationalError(
            1105, "errCode = 2, detailMessage = Access denied; you need (at least one of) the SELECT privilege(s)"
        )
        self.assertTrue(is_access_denied_error(exc))

    def test_failed_login_and_network_errors_are_not_access_denied(self) -> None:
        self.assertFalse(is_access_denied_error(_LOGIN_FAILED))
        self.assertFalse(is_access_denied_error(_UNREACHABLE))
        self.assertFalse(is_access_denied_error(RuntimeError("boom")))

    def test_doris_column_privilege_message_under_generic_code_is_access_denied(self) -> None:
        exc = pymysql.err.OperationalError(
            1105,
            "errCode = 2, detailMessage = Permission denied: user [alice] does not have "
            "privilege for [SELECT] command on [internal].[dw].[orders].[amount]",
        )
        self.assertTrue(is_access_denied_error(exc))

    def test_storage_permission_error_is_not_access_denied(self) -> None:
        exc = pymysql.err.OperationalError(
            1105, "errCode = 2, detailMessage = open file /data/seg_0.dat failed: Permission denied"
        )
        self.assertFalse(is_access_denied_error(exc))

    def test_wrapped_failed_login_without_code_is_not_access_denied(self) -> None:
        wrapped = RuntimeError("Access denied for user 'alice'@'10.0.0.5' (using password: YES)")
        self.assertFalse(is_access_denied_error(wrapped))


class ToolErrorMappingTests(unittest.TestCase):
    def test_execute_query_reports_permission_denied(self) -> None:
        err = _error(_run(execute_query(_FailingPool(_TABLE_DENIED), "SELECT * FROM dw.orders")))
        self.assertEqual(err["code"], "PERMISSION_DENIED")
        self.assertIn("grant SELECT", err["message"])
        self.assertIn("SELECT command denied", err["message"])

    def test_execute_query_keeps_connection_error_for_network_failures(self) -> None:
        err = _error(_run(execute_query(_FailingPool(_UNREACHABLE), "SELECT 1")))
        self.assertEqual(err["code"], "CONNECTION_ERROR")

    def test_execute_query_keeps_connection_error_for_failed_login(self) -> None:
        err = _error(_run(execute_query(_FailingPool(_LOGIN_FAILED), "SELECT 1")))
        self.assertEqual(err["code"], "CONNECTION_ERROR")

    def test_query_metric_names_the_workspace(self) -> None:
        compiler = MagicMock()
        compiler.compile.return_value = ("SELECT SUM(amount) AS revenue FROM dw.orders", "mf query", None)
        err = _error(
            _run(
                query_metric(
                    compiler,
                    _FailingPool(_DB_DENIED),
                    ["revenue"],
                    workspace="platform",
                )
            )
        )
        self.assertEqual(err["code"], "PERMISSION_DENIED")
        self.assertIn("workspace 'platform'", err["message"])
        self.assertIn("Access denied for user 'alice'", err["message"])

    def test_discovery_tools_report_permission_denied(self) -> None:
        pool = _FailingPool(_DB_DENIED)
        for result in (
            _run(list_databases(pool)),
            _run(list_tables(pool, "dw")),
            _run(describe_table(pool, "dw", "orders")),
        ):
            self.assertEqual(_error(result)["code"], "PERMISSION_DENIED")


if __name__ == "__main__":
    unittest.main()
