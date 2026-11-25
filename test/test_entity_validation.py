"""Offline tests: staging Validate rejects semantic models with bad entities (#10)."""

import sys
import tempfile
import unittest
from pathlib import Path

_SRC = Path(__file__).resolve().parent.parent / "src"
if str(_SRC) not in sys.path:
    sys.path.insert(0, str(_SRC))

from store import seed  # noqa: E402
from store.bootstrap import bootstrap_from_yaml  # noqa: E402

_EXAMPLE_FILES = {
    "orders.yaml": seed._ORDERS_YAML,
    "users.yaml": seed._USERS_YAML,
    "products.yaml": seed._PRODUCTS_YAML,
    "project.yaml": seed._PROJECT_YAML,
}

_PROJECT = """time_config:
  calendar:
    - table: dw.date_dim
      column: dt
      grain: day
"""


def _orders(entities: str) -> str:
    return f"""semantic_model:
  name: orders
  db_table: dw.orders
  defaults:
    agg_time_dimension: order_date
{entities}
  dimensions:
    - name: order_date
      type: time
      type_params:
        time_granularity: day
  measures:
    - name: total_amount
      expr: amount
      agg: sum
---
metric:
  name: revenue
  type: simple
  type_params:
    measure: total_amount
"""


class EntityValidationTests(unittest.TestCase):
    def _bootstrap(self, files: dict[str, str], check_entities: bool) -> tuple[bool, str]:
        with tempfile.TemporaryDirectory() as models, tempfile.TemporaryDirectory() as ws:
            for name, content in files.items():
                (Path(models) / name).write_text(content, encoding="utf-8")
            return bootstrap_from_yaml(Path(models), Path(ws), check_entities=check_entities)

    def _models(self, entities: str) -> dict[str, str]:
        return {"orders.yaml": _orders(entities), "project.yaml": _PROJECT}

    def test_bundled_example_still_validates(self) -> None:
        ok, err = self._bootstrap(_EXAMPLE_FILES, check_entities=True)
        self.assertTrue(ok, err)

    def test_valid_primary_entity_passes(self) -> None:
        ok, err = self._bootstrap(
            self._models("  entities:\n    - name: order\n      type: primary\n      expr: order_id"),
            check_entities=True,
        )
        self.assertTrue(ok, err)

    def test_empty_entity_list_is_rejected(self) -> None:
        ok, err = self._bootstrap(self._models("  entities: []"), check_entities=True)
        self.assertFalse(ok)
        self.assertIn("`orders` (orders.yaml) declares no entities", err)
        self.assertNotIn("does not define a primary entity", err)

    def test_foreign_entity_without_primary_is_rejected(self) -> None:
        ok, err = self._bootstrap(
            self._models("  entities:\n    - name: user\n      type: foreign\n      expr: user_id"),
            check_entities=True,
        )
        self.assertFalse(ok)
        self.assertIn("does not define a primary entity", err)
        self.assertIn("(orders.yaml)", err)

    def test_empty_entity_expr_is_rejected(self) -> None:
        ok, err = self._bootstrap(
            self._models("  entities:\n    - name: order\n      type: primary\n      expr: ''"),
            check_entities=True,
        )
        self.assertFalse(ok)
        self.assertIn("Entity `order` in semantic model `orders` (orders.yaml) has an empty expr", err)

    def test_reload_path_keeps_accepting_published_models(self) -> None:
        # Published workspaces are reloaded without the entity check, so a
        # model committed before this check existed keeps loading.
        ok, err = self._bootstrap(self._models("  entities: []"), check_entities=False)
        self.assertTrue(ok, err)


if __name__ == "__main__":
    unittest.main()
