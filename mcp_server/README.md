# IAP Platform — MCP

## Web Search (`iap-web-search`)

- **MCP 入口**：`mcp_server/run_web_search_mcp.py`（Cursor 透過 `.cursor/mcp.json` 啟動）
- **容器內腳本**：`iap_backend/iap_mcp/web_search_server.py`（需 `iap_backend_dev` 運行中）
- **工具**：`search_web(query, max_results=8)` → JSON（title, url, snippet）
- **搜尋實作**：`iap_backend/common/web_search.py`（預設 `chain`：SearXNG → DuckDuckGo fallback）

### 前置條件

1. **Docker 後端容器運行中**（推薦）：
   ```bash
   ./scripts/compose-dev.sh up -d iap_searxng iap_backend
   ```
2. Cursor 重載 MCP：`Settings → MCP → intelligent-ai-web-search → Restart`

SearXNG 設定目錄：`searxng/settings.yml`（需啟用 `json` format）。除錯 UI：`http://localhost:${SEARXNG_PORT:-8088}`。

環境變數（見 `dev.env`）：
- `WEB_SEARCH_PROVIDER=chain`（`searxng` | `ddgs` | `chain`）
- `SEARXNG_URL=http://iap_searxng:8080`

若容器未運行，launcher 會 fallback 到本機 Python（需 `pip install mcp ddgs`）。

### 依賴

```bash
pip install mcp ddgs
```

（已加入 `iap_backend/python_env/requirements.in`）

### HTTP API（與 MCP 同源）

`POST /api/v1/common/web_search`

```json
{ "query": "Elasticsearch hybrid search", "max_results": 5 }
```
