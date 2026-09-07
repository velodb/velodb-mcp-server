# VeloDB MCP Compact Query Guide

This reference is optional. Do not call `get_query_guide` or
`check_service_health` before routine queries.

## Choose a path

Use semantic tools for governed business metrics such as counts, sums, rates,
averages, rankings, and trends:

```text
list_metrics(workspace)
  → list_dimensions_for_metric(workspace, metric_name)
  → query_metric(workspace, metrics, group_by, where, order_by, limit, having)
```

Use direct VeloDB tools for explicit SQL, schema exploration, full-text search,
or when no semantic metric matches:

```text
list_databases()
  → list_tables(database)
  → describe_table(database, table)
  → execute_query(sql, database)
```

Skip discovery calls when the user already supplied the database, table,
columns, or SQL needed for the request.

## Semantic rules

- `workspace` is required; use the workspace named by the user, or `example`
  for the bundled sample.
- Call `list_metrics` only when the matching metric is not already known.
- Before grouping, use `list_dimensions_for_metric` to verify dimensions.
- `query_metric` checks `semantic_enabled`, loads the workspace on demand, and
  reloads it when the published semantic version changes. No health preflight
  is needed.
- `where` accepts SQL predicates or a JSON object. `having` accepts plain SQL
  comparisons against aggregate metric names.
- If no metric matches, use read-only SQL and tell the user that the result is
  not governed by the semantic model.

## Diagnostics

Call `check_service_health` only:

- after a tool returns `CONNECTION_ERROR`;
- after an unexplained `SERVICE_NOT_READY`; or
- when the user explicitly asks for service status.

It checks VeloDB with `SELECT 1`, discovers semantic workspaces, and refreshes
their runtime state so its result agrees with subsequent semantic tool calls.
This potentially expensive check is acceptable because it is diagnostic-only.

Do not call health for `UNAUTHENTICATED`, `PERMISSION_DENIED`, `INVALID_SQL`,
`INVALID_PARAMS`, `METRIC_NOT_FOUND`, `QUERY_TIMEOUT`, or
`SEMANTIC_DISABLED`; handle the returned error directly.

If the MCP endpoint itself is unreachable, this tool cannot run. Use the
deployment platform or load balancer's network/liveness check instead.
