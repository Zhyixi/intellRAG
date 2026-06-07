# IAP Platform — MCP

## Web Search (`iap-web-search`)

- **腳本**：`mcp_server/web_search_server.py`
- **工具**：`search_web(query, max_results=8)` → JSON（title, url, snippet）
- **Cursor 設定**：專案已含 `.cursor/mcp.json`，重載 MCP 後即可在 Agent 使用。

### 依賴

```bash
pip install mcp duckduckgo-search
```

（已加入 `iap_backend/python_env/requirements.in`）

### HTTP API（與 MCP 同源）

`POST /api/v1/common/web_search`

```json
{ "query": "Elasticsearch hybrid search", "max_results": 5 }
```
