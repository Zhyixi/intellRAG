# IAP Platform — 專案交接文件

> 品牌名稱：**IAP Platform**（Intelligent AI Platform）  
> 目錄與 Compose 服務統一使用 `iap_*` 前綴（`iap_backend`、`iap_elasticsearch`、`iap_frontend`）。  
> 本文件整合專案內原有 Markdown 說明，供新接手人員快速理解架構、部署與維運。

---

## 1. 專案簡介

本專案為 **IAP Platform**，以 **IntelliAgnet**（NotebookLM 风格）个人笔记本为主产品。

| 模組 | 说明 |
|------|------|
| **iap_frontend** | Streamlit — 登录/注册、我的笔记本、文档上传、个人管理 |
| **iap_backend** | FastAPI — 认证、Notebook RAG、文档 Embedding、Langfuse 用量 |
| **iap_elasticsearch** | 每用户索引 `iap_nb_{user_id}_file` |
| **user_uploads** | 用户上传文件目录（Compose 挂载） |
| **Langfuse** | Token/费用追踪（按 userId 聚合） |

**Notebook 主要 API 前缀：** `/api/v1/auth/*`、`/api/v1/notebook/*`、`/api/v1/account/*`

**环境变量（dev.env）：** `JWT_SECRET`、`ENCRYPTION_KEY`（用户 OpenAI Key 加密）、`LANGFUSE_PUBLIC_KEY` / `LANGFUSE_SECRET_KEY`

---

## 2. 系統架構（高層）

```
[使用者瀏覽器]
      │
      ▼
[Streamlit 前端 :8501] ──HTTP──▶ [FastAPI 後端 :44000]
                                      │
                    ┌─────────────────┼─────────────────┐
                    ▼                 ▼                 ▼
              [MongoDB]          [MySQL]           [Redis]
              對話歷史            員工/統計           快取
                    │
                    ▼
              [Elasticsearch] ◀── BGE-M3 Embedding
              向量 + BM25 混合檢索
                    │
                    ▼
              [vLLM / Ollama]  ← Langfuse 追蹤
              LLM 摘要 / 意圖 / 翻譯
```

**Agent 編排**：LangGraph `StateGraph`（`services/notebook_graphs.py`）  
**RAG 核心**：`services/rag_service.py`（ES hybrid：BM25 + KNN + RRF）  
**追蹤**：Langfuse 4.x + `CallbackHandler` / `@observe`

---

## 3. 目錄結構

```
intellRAG/
├── ReadMe.md                 # 本文件（交接總覽）
├── dev.env.example / prod.env.example  # Compose 範本（可提交）；複製為 dev.env / prod.env
├── dev.env / prod.env        # 實際 Compose 變數（已 gitignore，含密碼與 proxy）
├── docker-compose.yml        # 一鍵啟動全部服務
├── scripts/
│   ├── compose.sh            # 核心：dev|prod + 任意 docker compose 子命令
│   ├── compose-dev.sh        # 開發入口（讀 dev.env + proxy）
│   ├── compose-prod.sh       # 正式入口（讀 prod.env + proxy）
│   └── deploy-prod.sh        # 正式一鍵 up -d --build
├── iap.yaml               # K3s 部署清單
├── iap_frontend/          # Streamlit 前端
├── iap_backend/           # 後端主程式
│   ├── fast_api_service/     # FastAPI 入口 (main.py)
│   ├── services/             # Agent、RAG、翻譯
│   ├── etl/                  # 索引建置 ETL 服務
│   ├── python_env/           # Dockerfile、requirements.in / .txt
│   ├── configs/              # config.ini、.env.dev / .env.prod
│   ├── tests/                # pytest 測試
│   └── docs/flowcharts/      # ES 檢索流程圖 (Mermaid)
├── iap_elasticsearch/     # ES 設定與 IK 插件 zip
├── rag_doc/                  # 文件庫
└── db/                       # 各資料庫 volume 掛載目錄
```

---

## 4. 環境設定檔說明

| 檔案 | 用途 |
|------|------|
| `dev.env` | 開發環境 Compose 變數（**含 http_proxy，build 時必用**） |
| `prod.env` | 正式環境 Compose 變數 |
| `iap_backend/configs/.env.dev` | 後端容器內 DB/ES/Ollama 連線（服務名如 `iap_mysql`） |
| `iap_backend/configs/.env.prod` | 正式後端連線 |
| `iap_backend/configs/config.ini` | 模型、RAG、日誌等非敏感預設 |
| `iap_frontend/configs/.env.dev` | 前端 `BACKEND_IP` / `BACKEND_PORT` |

**重要（proxy）**

- 公司環境需透過 proxy 下載 Docker 映像與 pip 套件 → 使用 `scripts/compose-dev.sh`（開發）或 `scripts/compose-prod.sh`（正式）  
- 容器內呼叫 **Docker 內部服務名**（如 `iap_backend`）**不可走 proxy**，否則 Squid 無法解析 DNS  
- `dev.env` 已設定 `no_proxy`；前端 API 請求程式內亦強制 `proxies=None`

**機密**：複製 `dev.env.example` → `dev.env`（`prod.env` 同理）；`dev.env`、`prod.env`、`configs/.env.*` 含密碼與 API Key，**勿提交**（已在 `.gitignore`）。交接時請透過安全管道另行提供金鑰。已追蹤的舊 `dev.env`/`prod.env` 需另做歷史清理。

---

## 5. 快速啟動（Docker Compose，建議方式）

### 5.0 `scripts/` 用途與啟動指令

**前置**：複製環境檔後再啟動（僅首次或新機器）

```bash
cp dev.env.example dev.env    # 開發
cp prod.env.example prod.env  # 正式
# 編輯對應 .env，填入 proxy、密碼、Langfuse 等
```

以下指令請在**專案根目錄**執行，且建議使用 **Git Bash / WSL / Linux**（會自動帶入 `dev.env` / `prod.env` 的 proxy）。

| 腳本 | 用途 | 是否自動 `up -d` |
|------|------|------------------|
| `compose.sh` | 核心包裝：第一個參數必須是 `dev` 或 `prod`，其後接任意 `docker compose` 子命令 | 否，需自行加 `up -d` 等 |
| `compose-dev.sh` | 等同 `compose.sh dev …`，讀 **`dev.env`**（測試／開發） | 否 |
| `compose-prod.sh` | 等同 `compose.sh prod …`，讀 **`prod.env`**（正式） | 否 |
| `deploy-prod.sh` | 正式環境一鍵：**`compose-prod.sh up -d --build`** 並顯示 `ps` | 是（含重建映像） |

#### 開發環境（`compose-dev.sh` / `compose.sh dev`）

```bash
chmod +x scripts/compose.sh scripts/compose-dev.sh

# 啟動全部服務（背景）
./scripts/compose-dev.sh up -d

# 程式或 Dockerfile 變更後，重建並啟動
./scripts/compose-dev.sh up -d --build

# 僅建置映像、不啟動
./scripts/compose-dev.sh build

# 狀態 / 日誌 / 停止
./scripts/compose-dev.sh ps
./scripts/compose-dev.sh logs -f iap_backend
./scripts/compose-dev.sh down

# 等同寫法（顯式指定 dev）
./scripts/compose.sh dev up -d
./scripts/compose.sh dev logs -f iap_mysql
```

#### 正式環境（`compose-prod.sh` / `compose.sh prod` / `deploy-prod.sh`）

```bash
chmod +x scripts/compose.sh scripts/compose-prod.sh scripts/deploy-prod.sh

# 一鍵：重建映像 + 背景啟動 + 列出容器（建議正式機首次或發版）
./scripts/deploy-prod.sh

# 或分步（與開發相同子命令，但讀 prod.env）
./scripts/compose-prod.sh up -d
./scripts/compose-prod.sh up -d --build
./scripts/compose-prod.sh build
./scripts/compose-prod.sh ps
./scripts/compose-prod.sh logs -f iap_backend
./scripts/compose-prod.sh down

# 等同寫法
./scripts/compose.sh prod up -d
./scripts/compose.sh prod down
```

#### Windows（PowerShell，無 bash 時）

需已存在 `dev.env` 或 `prod.env`。若公司網路需 proxy，請先在 PowerShell 匯出與 `dev.env` 相同的 `HTTP_PROXY` / `HTTPS_PROXY` / `NO_PROXY`，或改用 Git Bash 執行上方腳本。

```powershell
cd E:\intellRAG
docker compose --env-file dev.env up -d
docker compose --env-file dev.env up -d --build
docker compose --env-file dev.env ps
docker compose --env-file dev.env down

docker compose --env-file prod.env up -d --build
```

> **勿**在需 proxy 的環境直接執行裸 `docker compose up`（未帶 `--env-file` 且未 export proxy），build 可能拉不到基礎映像。  
> `scripts/` 內 `patch_*.py`、`rebuild_*.py` 為歷史 API 遷移用，**不是**日常啟動腳本。

**更名後清理舊容器／映像**（曾使用 `ipa_*`、`intelligent_ai_*` 前綴時）：

```powershell
# 預覽
.\scripts\cleanup-legacy-docker.ps1
# 執行（可選 -RemoveImages 刪除舊映像）
.\scripts\cleanup-legacy-docker.ps1 -Apply -RemoveImages
.\scripts\compose-dev.sh up -d --build
```

Compose 專案名為 `iap_dev` / `iap_prod`（見 `dev.env` 的 `COMPOSE_PROJECT_NAME`），容器與自訂映像為 `iap_backend:dev`、`iap_elasticsearch:dev` 等。

---

### 5.1 開發環境（一次啟動全部服務）

> 指令彙總見 **§5.0**；本節為首次啟動檢查清單。

```bash
cd /path/to/intellRAG
chmod +x scripts/compose-dev.sh

# 首次啟動：本地尚無 iap_backend / iap_frontend 映像時，會自動 build Dockerfile
# 並在映像內由 requirements.in 執行 pip-compile、下載安裝依賴（無需本機先 pip-compile）
./scripts/compose-dev.sh up -d

# Dockerfile 或 requirements.in 變更後，強制重建映像
./scripts/compose-dev.sh up -d --build

# 查看狀態
./scripts/compose-dev.sh ps

# 查看日誌
./scripts/compose-dev.sh logs -f iap_backend
./scripts/compose-dev.sh logs -f iap_frontend
```

> **勿**在需 proxy 的環境直接執行 `docker compose up`，否則 build 拉不到 `ubuntu:22.04` 等基礎映像。

### 5.2 開發環境服務入口（預設 `dev.env`）

| 服務 | URL / 埠號 |
|------|------------|
| **Streamlit 前端** | http://localhost:9504 |
| **後端 Swagger** | http://localhost:44000/docs |
| **後端 ReDoc** | http://localhost:44000/redoc |
| Mongo Express | http://localhost:8074 |
| Kibana | http://localhost:6600 |
| MySQL（主機） | localhost:4306 |
| Mongo（主機） | localhost:37017 |
| Redis（主機） | localhost:7379 |
| Elasticsearch（主機） | localhost:10200 |
| **Langfuse 追蹤 UI** | http://localhost:3000（`compose-dev.sh up -d` 一併啟動；API Key 於 UI 建立後填入 `dev.env`） |

### 5.3 正式環境

> 指令彙總見 **§5.0**。

```bash
chmod +x scripts/compose-prod.sh scripts/deploy-prod.sh

# 一鍵部署（build + up -d）
./scripts/deploy-prod.sh

# 或分步執行（與開發環境相同子命令，但讀 prod.env）
./scripts/compose-prod.sh build
./scripts/compose-prod.sh up -d
./scripts/compose-prod.sh ps
./scripts/compose-prod.sh logs -f iap_backend
./scripts/compose-prod.sh down
```

> **勿**在需 proxy 的環境直接執行 `docker compose --env-file prod.env up`，請一律透過 `compose-prod.sh`。

| 項目 | dev | prod（`prod.env`） |
|------|-----|-------------------|
| 前端埠 | 9504 | 8504 |
| 後端埠 | 44000 | 43999 |
| Mongo | 37017 | 27017 |
| MySQL | 4306 | 3306 |

### 5.4 本機開發（不進容器）

```bash
# 後端
cd iap_backend/fast_api_service
export ENV=dev
uvicorn main:app --host 0.0.0.0 --port 44000 --reload

# 前端（需將 configs/.env.dev 的 BACKEND_IP 改為 127.0.0.1）
cd iap_frontend
export ENV=dev
streamlit run main.py --server.address=0.0.0.0 --server.port=8501
```

---

## 6. 前端（Streamlit）

- **入口**：`iap_frontend/main.py`  
- **API 封裝**：`iap_frontend/HandleRequest/iap.py`  
- **回應轉換**：`iap_frontend/HandleRequest/api_adapter.py`（將後端 nested `relatedFile` 展平供 UI 表格使用）  
- **登入**：LDAP（`CompanyMember.py`）+ 本地 `Employee.csv` 白名單；員工清單 API `/emp` 未實作時 fallback CSV  
- **Docker 內連後端**：`BACKEND_IP=iap_backend`、`BACKEND_PORT=44000`（由 compose 注入）

主要 API 呼叫：

- `POST /api/v1/iap/retrieve` — 檢索  
- `GET /api/v1/iap/history_session_id` — 歷史 session 列表  
- `GET /api/v1/iap/history_session` — 單一 session 對話  
- `GET /api/v1/iap/get_image` — 圖片  
- `GET /api/v1/common/check_backend` — 健康檢查  

---

## 7. 後端 API

### 7.1 路由前綴

| 前綴 | 模組 | 說明 |
|------|------|------|
| `/api/v1/auth` | `fast_api_service/api/auth/` | 注册、登录 |
| `/api/v1/notebook` | `fast_api_service/api/notebook/` | 对话、历史、文档上传 |
| `/api/v1/account` | `fast_api_service/api/account/` | API Key、用量 |
| `/api/v1/common` | `fast_api_service/api/common_api/` | 健康检查、翻译、网络搜索 |

### 7.2 Notebook 重要端点

- `POST /api/v1/notebook/chat/retrieve` — 对话检索  
- `POST /api/v1/notebook/chat/stream` — 流式对话  
- `POST /api/v1/notebook/documents/upload` — 文档上传与 embedding  
- `GET /api/v1/notebook/history/session_ids` — 历史 session 列表  

### 7.3 启动与依赖

- 容器 entrypoint：`iap_backend/entrypoint.sh`（dev/prod 均执行 `fast_api_service/main.py`）  
- DI 容器：`iap_backend/containers.py`  
- Mongo 对话集合：`nb_chat_history`、文档 `nb_documents`

---

## 8. 資料庫維運

### 8.1 MySQL

- **開發（Compose）**：`iap_mysql`，root 密碼見 `dev.env` 的 `MYSQL_ROOT_PASSWORD`  
- **資料庫名**：`LLMFramework`（`MYSQL_DATABASE`）  
- **重建資料庫**（見原 `ReadMeMySQL.md`）：

```bash
docker exec -i iap_mysql_dev mysql -u root -p123456 -e "DROP DATABASE IF EXISTS LLMFramework"
docker exec -i iap_mysql_dev mysql -u root -p123456 -e "CREATE DATABASE LLMFramework CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;"
# 若有備份檔：
docker exec iap_mysql_dev sh -c "mysql -u root -p123456 LLMFramework < /var/lib/mysql/LLMFramework_backup.sql"
```

### 8.2 MongoDB

```bash
docker exec -it iap_mongo_dev mongosh -u root -p 123456 --authenticationDatabase admin
```

- 聊天歷史：`LLM.nb_chat_history`、文档 `LLM.nb_documents`

### 8.3 Redis

```bash
redis-cli -h localhost -p 7379 -a 123456 ping
# 預期：PONG
```

### 8.4 Elasticsearch

- **帳密（預設）**：`elastic` / `123456789`  
- **IK 分詞**：映像 build 時安裝 `elasticsearch-analysis-ik-9.0.1.zip`  
- **索引命名**：Notebook 用户文档 `iap_nb_{user_id}_file`  

詳細備份還原、快照、Kibana 使用者設定見下方 **§10**（整理自 `ReadMeEs.md`）。

---

## 9. 測試

測試目錄：`iap_backend/tests/`

```bash
cd iap_backend
pytest                          # 預設排除 slow / manual
pytest tests/unit -q
pytest -m api
pytest -m evaluation
pytest -m performance
pytest --collect-only
```

| 目錄 | 說明 |
|------|------|
| `tests/api/` | API 整合測試 |
| `tests/unit/` | 單元測試 |
| `tests/integration/` | 手動/整合腳本 |
| `tests/evaluation/` | RAG 評估 |
| `tests/performance/` | 效能測試 |
| `tests/scripts/` | 維運腳本 |

---

## 10. Elasticsearch 維運速查

> 整理自 `ReadMeEs.md`

### 10.1 集群健康

```bash
curl -u elastic:123456789 "http://localhost:10200/_cluster/health?pretty"
curl -u elastic:123456789 "http://localhost:10200/_cat/nodes?v"
curl -u elastic:123456789 "http://localhost:10200/_cat/shards?v"
```

### 10.2 快照備份與還原

```bash
# 1. 查看快照庫
curl -u elastic:123456789 -X GET "http://localhost:10200/_snapshot/_all?pretty"

# 2. 註冊快照庫（location 對應容器內 /usr/share/elasticsearch/snapshots）
curl -u elastic:123456789 -X PUT "http://localhost:10200/_snapshot/my_backup" \
  -H 'Content-Type: application/json' -d'{"type":"fs","settings":{"location":"/usr/share/elasticsearch/snapshots","compress":true}}'

# 3. 建立快照
curl -u elastic:123456789 -X PUT "http://localhost:10200/_snapshot/my_backup/all_index_snapshot" \
  -H 'Content-Type: application/json' -d'{"include_global_state":false,"partial":true}'

# 4. rsync 至目標機器（路徑依環境調整）
sudo rsync -azxvP --delete iap_elasticsearch/snapshots/ user@host:/path/to/iap/iap_elasticsearch/snapshots/

# 5. 目標機註冊並還原
curl -u elastic:123456789 -X POST "http://localhost:10200/_snapshot/my_backup/all_index_snapshot/_restore" \
  -H 'Content-Type: application/json' -d'{"indices":"*"}'
```

### 10.3 常用維護

```bash
# 刪除索引
curl -u elastic:123456789 -X DELETE "http://localhost:10200/iap_ae"

# 調整磁碟水位
curl -X PUT "http://localhost:10200/_cluster/settings" -H 'Content-Type: application/json' -d'{
  "transient": {
    "cluster.routing.allocation.disk.watermark.low": "95%",
    "cluster.routing.allocation.disk.watermark.high": "97%",
    "cluster.routing.allocation.disk.watermark.flood_stage": "98%"
  }
}'
```

### 10.4 ES 檢索流程圖

位置：`iap_backend/docs/flowcharts/`

- `es_retrieve_overview.mmd` / `.svg` — 整體 API 呼叫鏈  
- `es_retrieve_internal.mmd` / `.svg` — 單 index 內部 BM25 + KNN + RRF  

可用 [Mermaid Live Editor](https://mermaid.live) 重新匯出。

---

## 11. K3s / Kubernetes 部署

> 整理自 `k3sReadMe.md`（完整概念與 Pod 狀態表請見原檔）

### 11.1 部署前準備

```bash
# Elasticsearch 需要（主機上執行一次）
sudo nano /etc/sysctl.conf
# 加入：vm.max_map_count=262144
sudo sysctl -p
```

### 11.2 部署與檢查

```bash
sudo -E kubectl apply -f iap.yaml
sudo -E kubectl get pods
sudo -E kubectl get jobs    # elasticsearch-setup 應 1/1 SUCCESSFUL
sudo -E kubectl logs <pod-name>
sudo -E kubectl describe pod <pod-name>
```

### 11.3 撤銷部署

```bash
sudo -E kubectl delete -f iap.yaml
```

### 11.4 K3s 常見問題

| 問題 | 處理 |
|------|------|
| NodePort 超出 30000–32767 | 修改 `/etc/systemd/system/k3s.service`，加入 `--kube-apiserver-arg="service-node-port-range=1-65535"` 後重啟 k3s |
| 服務名含底線 | K8s DNS 不支援 `_`，須用 `iap-mysql` 而非 `iap_mysql` |
| 內部連線 Timeout | ConfigMap 的 `NO_PROXY` 須含所有叢集內服務名 |
| Pod 刪不掉 | `kubectl delete pod <name> --force --grace-period=0` |
| CrashLoopBackOff | `kubectl logs` + `kubectl describe pod` |

### 11.5 K3s 服務埠（參考 `k3sReadMe.md`）

- 後端 API：44000  
- Mongo Express：8074  
- Kibana：30600（依 yaml 為準）  
- MySQL：4306  

---

## 12. Langfuse 追蹤（簡述）

- 環境變數：`LANGFUSE_SECRET_KEY`、`LANGFUSE_PUBLIC_KEY`、`LANGFUSE_BASE_URL`（見 `dev.env` / `prod.env`）  
- UI：依 `LANGFUSE_BASE_URL`（例：`http://10.110.209.10:3000`）  
- 後端：Langfuse 4.x，`CallbackHandler` 掛在 LangGraph；AE 部分節點有 `@observe`  
- **注意**：容器內 LLM 對外呼叫可走 proxy；對內 `iap_backend` 不可走 proxy  

---

## 13. RAG 與檢索（開發參考）

> 節錄自 `iap_backend/ReadMe.md`

### 13.1 檢索流程（`es_retrive`）

1. LLM 結構化關鍵字提取（含 TTL 快取）  
2. BM25 lexical 檢索  
3. 有命中 → scoped semantic KNN；無命中 → 全索引 fallback  
4. RRF 融合（lexical_weight=0.7）  

### 13.2 優化方向（待辦）

- Query Rewriting / Multi-Query RAG  
- Metadata 條件篩選  
- Cross-encoder Reranker（`bge-reranker-v2-m3` 已配置，主路徑尚未接入）  
- 資料品質與 chunk 粒度審查  
- Prompt 版本管理（Langfuse `get_prompt` + fallback 硬編碼混用）  

### 13.3 評估指標

- **Hit Rate**：相關結果是否出現在前 n 名  
- **MRR**：相關結果平均倒數排名  

---

## 14. 常見問題排除

### 14.1 Docker / 網路

| 現象 | 原因 | 處理 |
|------|------|------|
| build 時 `auth.docker.io` EOF | 未走 proxy | 使用 `./scripts/compose-dev.sh build` |
| `pull access denied for iap_frontend` | Compose 誤從 Hub 拉本地映像 | 已設 `pull_policy: never`，需本地 build |
| 前端「無法連線後端」 | proxy 攔截 `iap_backend` DNS | 確認 `NO_PROXY` 與 `HandleRequest/iap.py` 直連 |
| `chat_history` KeyError | 後端失敗時未初始化 session | 已於 `main.py` 加入 `ensure_chat_session_state()` |

### 14.2 應用

| 現象 | 處理 |
|------|------|
| CORS 錯誤 | 在 `dev.env` 設定 `CORS_ORIGINS` 含前端實際 URL |
| 檢索無結果 | 確認 ES 索引已 ETL；查 `iap_backend` log |
| 圖片無法顯示 | 確認 `/app/db/images/iap` 路徑與 `get_image` 參數格式 |

---

## 15. 維運指令速查

### Docker

完整說明與 `compose.sh` 寫法見 **§5.0**。

```bash
# 開發（compose-dev.sh = compose.sh dev）
./scripts/compose-dev.sh up -d
./scripts/compose-dev.sh up -d --build
./scripts/compose-dev.sh down
./scripts/compose-dev.sh ps
./scripts/compose-dev.sh logs -f <service>

# 正式（deploy-prod.sh = compose-prod.sh up -d --build）
./scripts/deploy-prod.sh
./scripts/compose-prod.sh up -d
./scripts/compose-prod.sh down
./scripts/compose-prod.sh logs -f <service>
docker commit iap_backend_dev iap_backend:backup
docker save -o llm_backend.docker iap_backend:dev
docker load -i llm_backend.docker
```

### 依賴更新

Docker 建置時會依 `requirements.in` 在映像內自動 `pip-compile` 並安裝。下列指令僅供**本機開發**鎖定版本或跑測試：

```bash
# 後端執行期（可選：提交 requirements.txt 供團隊對照）
cd iap_backend/python_env
pip-compile requirements.in -o requirements.txt

# 後端開發／測試（含 pytest，不裝入映像）
pip-compile requirements-dev.in -o requirements-dev.txt
pip install -r requirements-dev.txt

# 前端（可選）
cd iap_frontend/python_env
pip-compile requirements.in -o requirements.txt
```

### 資料同步（範例）

```bash
sudo rsync -azxvP db/images/ user@host:/path/to/iap/db/
```

### 連線測試

```bash
curl http://localhost:44000/api/v1/common/check_backend
netstat -tulnp | grep 44000
```

---

## 16. 相關圖檔

ES 檢索流程 Mermaid / SVG 位於 `iap_backend/docs/flowcharts/`（`es_retrieve_overview.*`、`es_retrieve_internal.*`）。專案說明已統一於本文件，不再維護分散的 Markdown。

---

## 17. 交接檢查清單

- [ ] 取得 `dev.env` / `prod.env` 及後端 `configs/.env.*` 最新密鑰（安全管道）  
- [ ] 確認可執行 `./scripts/compose-dev.sh build && ./scripts/compose-dev.sh up -d`（開發）  
- [ ] 確認可執行 `./scripts/deploy-prod.sh` 或 `./scripts/compose-prod.sh build && ./scripts/compose-prod.sh up -d`（正式）  
- [ ] 瀏覽器開啟前端，LDAP 登入測試一筆檢索  
- [ ] 開啟 http://localhost:44000/docs 確認 API  
- [ ] 確認 Langfuse 是否可看到 trace（若已配置）  
- [ ] 確認 ES / Mongo / MySQL 資料 volume 備份策略  
- [ ] 閱讀 `iap.yaml` 了解 K3s 正式部署差異（若使用）  
- [ ] 確認 vLLM / LiteLLM 閘道位址（`containers.py` 內 `10.110.209.10:4000`）  

---

## 18. 聯絡與備註

- 前端對外埠以實際 `FRONTEND_PORT` 為準（dev 預設 **9504**，若環境使用 **8504** 請改 `dev.env`）  
- API **request/response 契約**為對外服務介面，變更前需與呼叫方（含 Streamlit）協調  
- 本文件最後整理日期：2026-06-01  

---

*文件結束*
