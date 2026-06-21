# CLAUDE.md

This file provides guidance to Claude Code (claude.ai/code) when working with code in this repository.

## 專案概覽

**IAP Platform（Intelligent AI Platform）** — 以 NotebookLM 風格的個人筆記本為主產品，提供 RAG 文件問答、串流對話、文件 Embedding 上傳與 Langfuse 用量追蹤。

- **iap_backend**：FastAPI（Python），RAG 核心、LangGraph Agent 編排、文件 ETL
- **iap_frontend**：React + Vite（Node.js），SPA 前端，通過 Vite dev server 或 preview 模式提供
- **iap_elasticsearch**：ES 9.x + IK 分詞，Notebook 用戶索引 `iap_nb_{user_id}_file`
- **資料庫**：MongoDB（對話歷史）、MySQL（用戶帳號）、Redis（快取 / Job 狀態）

---

## 常用指令

### Docker Compose（推薦）

```bash
# 複製環境檔（首次）
cp dev.env.example dev.env

# 啟動開發環境（Git Bash / WSL）
./scripts/compose-dev.sh up -d
./scripts/compose-dev.sh up -d --build   # Dockerfile 或 requirements.in 變更後重建

# PowerShell（無 bash 時）
docker compose --env-file dev.env up -d
docker compose --env-file dev.env up -d --build

# 查看狀態 / 日誌
./scripts/compose-dev.sh ps
./scripts/compose-dev.sh logs -f iap_backend
./scripts/compose-dev.sh logs -f iap_frontend
./scripts/compose-dev.sh down
```

> 公司網路環境下，**必須**使用 `compose-dev.sh`，否則 build 時無法透過 proxy 拉取映像。

### 本機開發（不進容器）

```bash
# 後端
cd iap_backend/fast_api_service
export ENV=dev
uvicorn main:app --host 0.0.0.0 --port 44000 --reload

# 前端（需先將 iap_backend/configs/.env.dev 的 BACKEND_IP 改為 127.0.0.1）
cd iap_frontend
export ENV=dev
npm run dev -- --host 0.0.0.0 --port 8501
```

### 測試（後端）

```bash
cd iap_backend
pytest                     # 排除 slow / manual 標記
pytest tests/unit -q       # 單元測試
pytest -m api              # API 整合
pytest -m evaluation       # RAG 評估
pytest -m performance      # 效能
pytest --collect-only      # 列出所有測試
```

### E2E 測試（前端）

```bash
cd iap_frontend
E2E_BASE_URL=http://localhost:8501 E2E_BACKEND_URL=http://localhost:44000 pytest tests/e2e/
```

### 前端建置

```bash
cd iap_frontend
npm run dev      # Vite dev server
npm run build    # 正式建置
npm run preview  # 預覽正式建置結果
```

### 依賴管理

Docker build 時 `requirements.in` 會在映像內自動執行 `pip-compile` 並安裝，**本機不需手動執行**。若要本機鎖定版本：

```bash
cd iap_backend/python_env
pip-compile requirements.in -o requirements.txt
pip-compile requirements-dev.in -o requirements-dev.txt
pip install -r requirements-dev.txt
```

---

## 服務入口（開發環境預設）

| 服務 | URL |
|------|-----|
| 前端 | http://localhost:9504 |
| 後端 Swagger | http://localhost:44000/docs |
| Langfuse UI | http://localhost:3000 |
| Kibana | http://localhost:6600 |
| Mongo Express | http://localhost:8074 |

---

## 架構說明

### 請求流程

```
[瀏覽器 React App]
      │  JWT Bearer Token
      ▼
[FastAPI :44000]  → common.auth.get_current_user (JWT 驗證)
      │
      ├── /api/v1/notebook/chat/stream  → notebook_graphs.stream_notebook_chat
      │         └── LangGraph StateGraph（notebook_graphs.py）
      │               ├── load_memory → classify_intent
      │               ├── retrieve_docs（rag_service.py ES hybrid）
      │               ├── evaluate_coverage → offer_web_search / generate_from_docs
      │               └── suggest_followups
      │
      ├── /api/v1/notebook/documents/upload → document_worker.py（背景 ETL）
      │         └── PDF → MinerU/pypdf → embed（BGE-M3）→ ES index
      │
      └── /api/v1/account/* → Langfuse 用量聚合
```

### 後端核心模組

| 路徑 | 說明 |
|------|------|
| `iap_backend/fast_api_service/main.py` | FastAPI 入口，掛載所有 router，CORS 設定 |
| `iap_backend/containers.py` | dependency-injector DI 容器；所有 singleton（ES / Mongo / Redis / LLM）在此初始化 |
| `iap_backend/services/notebook_graphs.py` | LangGraph `StateGraph`；Notebook 對話的完整節點邏輯與路由 |
| `iap_backend/services/rag_service.py` | ES hybrid 檢索（BM25 + KNN + RRF）；`RetriveService` 與 `RAGService` |
| `iap_backend/services/document_worker.py` | 可暫停 / 恢復的文件 Embedding 背景處理；Job 狀態存 Redis + Mongo |
| `iap_backend/services/job_service.py` | Job pause / resume / cancel 狀態機（Redis）|
| `iap_backend/common/langfuse_tracing.py` | Langfuse 4.x 整合；`@observe` wrapper、prompt 管理、embedding 逐頁追蹤 |
| `iap_backend/configs/config.py` | 讀取 `configs/.env.{ENV}` + `config.ini`；所有連線參數由此集中匯出 |
| `iap_backend/repositories/repositories.py` | ES / Mongo / Redis / MySQL 的低階存取層 |

### LangGraph Notebook 節點流

`notebook_graphs.py` 的 `StateGraph` 主要節點順序：
1. `load_memory` → `check_memory_answer`（快速從記憶回答）
2. `classify_intent`（chitchat / general_knowledge / doc_query / unclear）
3. `retrieve_docs`（ES hybrid BM25+KNN+RRF）
4. `evaluate_coverage` → `generate_from_docs` 或 `offer_web_search`
5. `web_search`（SearXNG MCP）→ `generate_from_web`
6. `suggest_followups`

### ES 檢索流程

`rag_service.py` 的 `es_retrive`：
1. LLM 結構化關鍵字提取（含 Redis TTL 快取）
2. BM25 lexical 檢索
3. 有命中 → scoped semantic KNN；無命中 → 全索引 fallback KNN
4. RRF 融合（`lexical_weight=0.7`）

### 前端架構

前端為 React SPA（`iap_frontend/src/`），不使用路由器，靠 `useState(page)` 切換 3 個頁面：
- `NotebookPage`：對話視圖，SSE 串流接收（`streamChat`），含 web search 確認流程
- `DocumentsPage`：文件上傳 + Job 進度輪詢（每 2 秒）
- `AccountPage`：個人資料、OpenAI API Key 管理、Langfuse 用量圖表

API 呼叫全集中在 `iap_frontend/src/api.js`，所有字串 / 國際化在 `src/strings.js`。

### DI 容器（containers.py）

`Container` 為 process-wide singleton，持有：
- `ollama_general_llm` / `vllm_general_llm`：dev 用 Ollama/LiteLLM，prod 用 LiteLLM gateway（`10.110.209.10:4000`）
- `embedding_model`：啟動時依 GPU / 設定選擇 local（BGE-M3）或 cloud
- `langfuse_client` / `langfuse_handler`：僅當 `LANGFUSE_PUBLIC_KEY` + `LANGFUSE_SECRET_KEY` 存在時啟用
- `mongo_checkpointer`（`MongoDBSaver`）：LangGraph 長期記憶

### 環境變數

| 層級 | 檔案 | 用途 |
|------|------|------|
| Compose 層 | `dev.env` / `prod.env` | proxy、埠號、DB 密碼、Langfuse Key |
| 容器內後端 | `iap_backend/configs/.env.dev` | DB/ES/Ollama 連線（容器服務名如 `iap_mysql`）|
| 容器內前端 | `iap_frontend/configs/.env.dev` | `BACKEND_IP=iap_backend`、`BACKEND_PORT=44000` |
| 非敏感預設 | `iap_backend/config.yml` | 模型名稱、RAG chunk size、log 路徑等 |

關鍵環境變數：`JWT_SECRET`（≥32 字元）、`ENCRYPTION_KEY`（用戶 OpenAI Key AES 加密）、`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`。

### Proxy 注意事項

- 公司環境需走 `http_proxy` 拉取映像與 pip 套件 → 一律用 `compose-dev.sh`
- 容器內呼叫 Docker 內部服務名（`iap_backend`、`iap_mysql` 等）**不可走 proxy**（Squid 無法解析 Docker DNS）
- 前端 `api.js` 對後端的 fetch 強制不走 proxy；`document_worker.py` 中 requests 呼叫同理

### K3s 部署

`iap.yaml` 為 K3s 部署清單，服務名改用連字號（`iap-mysql` 而非 `iap_mysql`，K8s DNS 不支援底線）。部署前需在主機執行 `sysctl -w vm.max_map_count=262144`。

---

## MongoDB 集合

- `LLM.nb_chat_history`：對話歷史
- `LLM.nb_documents`：文件與 Embedding Job 狀態（含可恢復 checkpoint）
- LangGraph checkpoint：`MongoDBSaver`（`langgraph_checkpoints` 集合）
