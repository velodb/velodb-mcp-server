# 接入 SelectDB Doris MCP（LiteLLM 网关）

本项目通过 LiteLLM 网关接入远程 MCP server（SelectDB Doris），
LLM 可用 tool calling 查询 Doris 数据。

## MCP 连接方式

| 项 | 值 |
|---|---|
| transport | streamable-http |
| url | `https://albj8hpz.cn-beijing.aliyun.selectdb.cloud/mcp` |
| Authorization | `Bearer ${VELODB_MCP_AUTH_TOKEN}` |
| Accept | `application/json, text/event-stream` |

## 配置（litellm-config.yaml）

```yaml
# LLM 模型不用写死在 config 里，运行后用 /model/new 动态新增（存数据库，重启不丢）
# model_list: []

mcp_servers:
  velodb:                            # server 名（任意）
    transport: http                  # streamable-http → http（默认是 sse，必须显式写）
    url: https://albj8hpz.cn-beijing.aliyun.selectdb.cloud/mcp
    auth_type: bearer_token          # 自动生成 "Bearer ${VELODB_MCP_AUTH_TOKEN}"
    auth_value: os.environ/VELODB_MCP_AUTH_TOKEN
    static_headers:
      Accept: application/json, text/event-stream

general_settings:
  master_key: os.environ/LITELLM_MASTER_KEY                # 网关访问 key
  store_model_in_db: true            # 开启后才能用 /model/new 动态新增模型
```

## 动态新增 LLM 模型（不用改 config / 重启）

```bash
curl -X POST http://localhost:4000/model/new \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" -H "Content-Type: application/json" \
  -d '{
    "model_name": "gpt-4o-mini",
    "litellm_params": {
      "model": "openai/gpt-4o-mini",
      "api_key": "<你的 key>"
    }
  }'

# 查看已添加的模型
curl http://localhost:4000/v1/models -H "Authorization: Bearer ${LITELLM_MASTER_KEY}"
```

api_key 会自动加密存储；重启代理后模型依然在。

## 验证

```bash
# 列出已加载的 MCP 工具
curl "http://localhost:4000/mcp-rest/tools/list" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" -H "x-mcp-server: velodb" \
  -H "Accept: application/json, text/event-stream"

# 调用工具（server_id 从 tools/list 返回中取）
curl -X POST "http://localhost:4000/mcp-rest/tools/call" \
  -H "Authorization: Bearer ${LITELLM_MASTER_KEY}" -H "x-mcp-server: velodb" \
  -H "Content-Type: application/json" \
  -d '{"server_id":"<server_id>","name":"query_metric",
       "arguments":{"workspace":"example","metrics":["total_amount"]}}'
```

验证通过：加载 10 个工具（`get_query_guide` / `check_service_health` / `list_metrics` / `query_metric` / `execute_query` 等），`query_metric` 可正常返回数据。
