"""前端 UI 文案（繁體中文 / English）。"""

APP_TITLE = "IntelliAgnet"
APP_TAGLINE = "個人知識筆記本 / Personal Knowledge Notebook"
INIT_LOADING = "正在初始化… / Initializing…"
PLEASE_LOGIN = "請先登入 / Please sign in first"

# --- 導覽 ---
NAV_NOTEBOOK = "我的筆記本 / My Notebook"
NAV_DOCUMENTS = "文件 / Documents"
NAV_ACCOUNT = "個人管理 / Account"

# --- 登入 / 註冊 ---
TAB_LOGIN = "登入 / Sign In"
TAB_REGISTER = "註冊 / Sign Up"
LABEL_EMAIL = "電子郵件 / Email"
LABEL_PASSWORD = "密碼 / Password"
LABEL_DISPLAY_NAME = "暱稱（選填）/ Display name (optional)"
LABEL_PASSWORD_MIN = "密碼（至少 8 字元）/ Password (min. 8 characters)"
LABEL_CONFIRM_PASSWORD = "確認密碼 / Confirm password"
BTN_LOGIN = "登入 / Sign In"
BTN_REGISTER = "註冊 / Sign Up"
ERR_EMAIL_PASSWORD_REQUIRED = "請輸入電子郵件與密碼 / Please enter email and password"
ERR_LOGIN_FAILED = "登入失敗 / Sign in failed"
ERR_USER_INFO = "無法取得使用者資訊 / Could not load user profile"
SUCCESS_LOGIN = "登入成功 / Signed in successfully"
ERR_PASSWORD_MISMATCH = "兩次密碼不一致 / Passwords do not match"
ERR_PASSWORD_TOO_SHORT = "密碼至少 8 字元 / Password must be at least 8 characters"
ERR_REGISTER_FAILED = "註冊失敗 / Registration failed"
SUCCESS_REGISTER = "註冊成功，已自動登入 / Registered and signed in"

# --- 筆記本 ---
BTN_NEW_CHAT = "新建對話 / New chat"
CHAT_WELCOME = (
    "你好！我是你的個人筆記本助手。"
    "上傳文件後，我可以根據你的資料回答。\n\n"
    "Hello! I'm your personal notebook assistant. "
    "Upload documents and I'll answer based on your sources."
)
CHAT_HISTORY_LOADED = "已載入歷史對話。 / Previous conversation loaded."
CHAT_INPUT_PLACEHOLDER = "輸入你的問題… / Ask a question…"
SUGGESTED_QUESTIONS_TITLE = "建議追問 / Suggested follow-ups"
CHAT_THINKING = "思考中… / Thinking…"
CHAT_REQUEST_FAILED = "請求失敗 / Request failed"
CHAT_STATUS_PREFIX = "正在執行 / Running"
CHAT_TOOL_PREFIX = "工具 / Tool"
BTN_WEB_SEARCH = "上網搜尋 / Search the web"
WEB_SEARCH_HINT = "文件內找不到答案時，可選擇上網搜尋。 / Not in your docs? Search the web."
CITATIONS_TITLE = "引用來源 / Sources"
CITATION_LINE = "**[{i}]** {file} 第 {page} 頁 / Page {page} — {snippet}…"

# --- 文件 ---
DOCS_TITLE = "我的文件 / My Documents"
DOCS_CAPTION = (
    "上傳 PDF、Word、TXT 等文件，系統會自動 Embedding 並建立個人索引。\n\n"
    "Upload PDF, Word, TXT, etc. Files are embedded and indexed for you."
)
DOCS_UPLOADER = "選擇文件 / Choose file"
BTN_UPLOAD_INDEX = "開始上傳並索引 / Upload & index"
DOCS_UPLOAD_OK = "已上傳，任務 ID / Task ID: {job_id}…"
DOCS_UPLOAD_FAIL = "上傳失敗 / Upload failed (HTTP {code})"
DOCS_PROGRESS_TITLE = "索引進度 / Indexing progress"
DOCS_PROGRESS_PERCENT = "{pct}%"
DOCS_PROGRESS_PAGES = "第 {done} / {total} 頁 / Page {done} of {total}"
DOCS_INDEX_DONE = "索引完成 / Indexing complete"
DOCS_INDEX_FAILED = "處理失敗 / Processing failed"
DOCS_INDEX_PAUSED = "已暫停 / Paused"
DOCS_INDEX_INTERRUPTED = "工作中斷，可點「繼續索引」從斷點恢復 / Interrupted — click Resume"
DOCS_INDEX_STOPPED = "已停止，可繼續索引 / Stopped — resumable"
BTN_PAUSE_INDEX = "暫停 / Pause"
BTN_RESUME_INDEX = "繼續索引 / Resume"
BTN_STOP_INDEX = "停止 / Stop"
DOCS_LIST_TITLE = "已上傳文件 / Uploaded documents"
DOCS_EMPTY = "尚無文件，請先上傳。 / No documents yet. Upload one to get started."
BTN_DELETE_DOC = "刪除 / Delete {name}"
DOCS_COL_FILENAME = "filename"
DOCS_COL_LABELS = {
    "filename": "檔名 / Filename",
    "status": "狀態 / Status",
    "page_count": "頁數 / Pages",
    "indexed_chunks": "片段數 / Chunks",
    "created_at": "建立時間 / Created",
}

# --- 個人管理 ---
ACCOUNT_TITLE = "個人管理 / Account"
TAB_PROFILE = "個人資料 / Profile"
TAB_API_KEY = "API 金鑰 / API Key"
TAB_USAGE = "用量與費用 / Usage & cost"
ERR_LOAD_PROFILE = "無法載入使用者資訊 / Could not load profile"
LABEL_EMAIL_READONLY = "電子郵件 / Email"
LABEL_NICKNAME = "暱稱 / Display name"
BTN_SAVE_NAME = "儲存暱稱 / Save name"
SUCCESS_UPDATED = "已更新 / Updated"
ERR_UPDATE_FAILED = "更新失敗 / Update failed"
EXPANDER_PASSWORD = "修改密碼 / Change password"
LABEL_CURRENT_PASSWORD = "目前密碼 / Current password"
LABEL_NEW_PASSWORD = "新密碼 / New password"
BTN_UPDATE_PASSWORD = "更新密碼 / Update password"
SUCCESS_PASSWORD_UPDATED = "密碼已更新 / Password updated"
BTN_LOGOUT = "登出 / Sign out"
API_KEY_STATUS = "**狀態 / Status:**"
API_KEY_CONFIGURED = "已設定 ✓ / Configured"
API_KEY_NOT_CONFIGURED = "未設定（使用平台預設 Key）/ Not set (using platform key)"
API_KEY_LAST_UPDATED = "上次更新 / Last updated: {time}"
LABEL_OPENAI_KEY = "OpenAI API 金鑰 / OpenAI API Key"
BTN_SAVE_KEY = "儲存金鑰 / Save key"
BTN_CLEAR_KEY = "清除金鑰 / Clear key"
ERR_KEY_TOO_SHORT = "金鑰太短 / API key too short"
SUCCESS_KEY_SAVED = "已儲存（加密存放）/ Saved (encrypted)"
ERR_KEY_SAVE_FAILED = "儲存失敗 / Save failed"
SUCCESS_KEY_CLEARED = "已清除 / Cleared"
API_KEY_INFO = (
    "你的金鑰僅用於聊天與文件 Embedding，不會顯示給其他人。\n\n"
    "Your key is used only for chat and document embedding; it is never shown to others."
)
USAGE_LANGFUSE_DISABLED = "Langfuse 未設定，無法顯示用量。 / Langfuse not configured."
METRIC_TODAY_TOKENS = "今日 Tokens / Today's tokens"
METRIC_TODAY_COST = "今日費用 (USD) / Today's cost"
METRIC_MONTH_TOKENS = "本月 Tokens / This month"
METRIC_MONTH_COST = "本月費用 (USD) / This month"
CHART_TOKEN_TREND = "近 30 日 Token 趨勢 / Token trend (30 days)"
CHART_COST_TREND = "近 30 日費用趨勢 (USD) / Cost trend (30 days)"
USAGE_BY_MODEL = "依模型明細 / By model"
