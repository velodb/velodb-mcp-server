"""Tests for automatic query routing and on-demand semantic loading."""

from __future__ import annotations

import tempfile
import unittest
import sys
import inspect
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from config.loader import AppConfig
from server import _SERVER_INSTRUCTIONS, create_server


class TestSemanticConfiguration(unittest.TestCase):
    def test_app_config_has_no_semantic_routing_mode(self):
        with tempfile.TemporaryDirectory() as temporary:
            config_dir = Path(temporary)
            config = AppConfig(config_dir)

        self.assertFalse(hasattr(config, "semantic"))


class TestSemanticInstructions(unittest.TestCase):
    def test_instructions_describe_automatic_on_demand_routing(self):
        self.assertIn("load only when semantic tools", _SERVER_INSTRUCTIONS)
        self.assertIn("read-only SQL", _SERVER_INSTRUCTIONS)
        self.assertNotIn("query mode", _SERVER_INSTRUCTIONS.lower())

    def test_guide_and_health_are_not_query_preflight(self):
        self.assertIn(
            "Do not call get_query_guide or check_service_health as routine preflight",
            _SERVER_INSTRUCTIONS,
        )
        self.assertIn("CONNECTION_ERROR", _SERVER_INSTRUCTIONS)


class TestSemanticToolDescriptions(unittest.IsolatedAsyncioTestCase):
    def _server(self):
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        config_dir = Path(temporary.name) / "config"
        config_dir.mkdir()
        return create_server(
            config_dir=str(config_dir), config=AppConfig(config_dir)
        )

    async def test_sql_is_direct_and_semantics_are_on_demand(self):
        server = self._server()
        execute_query = await server.get_tool("execute_query")
        list_metrics = await server.get_tool("list_metrics")
        list_dimensions = await server.get_tool("list_dimensions_for_metric")
        query_metric = await server.get_tool("query_metric")
        check_health = await server.get_tool("check_service_health")

        self.assertIn("loading is not required", execute_query.description)
        self.assertIn("skip when it is already known", list_metrics.description)
        self.assertIn("Time-grain shortcuts", list_dimensions.description)
        self.assertIn("prefix order_by with '-' for DESC", query_metric.description)
        self.assertIn("where accepts a SQL predicate or JSON object", query_metric.description)
        self.assertIn("having is a plain SQL comparison", query_metric.description)
        self.assertIn("no query-guide or health preflight", query_metric.description)
        self.assertIn("Diagnostic only", check_health.description)
        self.assertIn("do not call before normal queries", check_health.description)

    async def test_health_actively_checks_semantic_workspaces(self):
        server = self._server()
        check_health = await server.get_tool("check_service_health")

        source = inspect.getsource(check_health.fn)
        self.assertIn("discover_workspaces", source)
        self.assertIn("ensure_fresh", source)
        self.assertIn("first_load=True", source)

    async def test_server_auth_does_not_eagerly_initialize_semantics(self):
        server = self._server()
        self.assertFalse(hasattr(server.auth, "_on_authenticated"))

    async def test_query_guide_is_optional_and_compact(self):
        server = self._server()
        query_guide = await server.get_tool("get_query_guide")

        self.assertIn("Optional", query_guide.description)
        self.assertIn("do not call as a normal preflight", query_guide.fn.__doc__)

        guide_path = _SRC / "skills" / "doris-mcp-skill.md"
        guide = guide_path.read_text(encoding="utf-8")
        self.assertLess(len(guide), 4_000)
        self.assertIn("Do not call", guide)
        self.assertIn("CONNECTION_ERROR", guide)


if __name__ == "__main__":
    unittest.main(verbosity=2)
