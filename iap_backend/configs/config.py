import os
import sys
import configparser
from pathlib import Path

from dotenv import load_dotenv

from common.platform import PLATFORM_NAME, PLATFORM_SLUG
from common.utils import get_config_dir

sys.path.extend([".", ".."])


def _normalize_env(raw_env: str | None) -> str:
    env = str(raw_env or "prod").strip().lower()
    if env in {"production", "prd"}:
        return "prod"
    if env in {"development"}:
        return "dev"
    return env


def _load_env_file(env: str) -> Path:
    env_file = Path(f"/app/configs/.env.{env}")
    if not env_file.exists():
        env_file = Path("/app/configs/.env.dev")
    load_dotenv(dotenv_path=str(env_file), override=False)
    return env_file


def _getenv(name: str, default: str | None = None) -> str | None:
    value = os.getenv(name)
    if value is None:
        return default
    value = value.strip()
    return value if value != "" else default


def _getenv_int(name: str, default: int) -> int:
    raw = _getenv(name)
    if raw is None:
        return default
    try:
        return int(raw)
    except (TypeError, ValueError):
        return default


def _safe_join_db_url(user: str | None, password: str | None, host: str | None, port: str | None, db: str | None) -> str:
    return f"mysql+pymysql://{user or ''}:{password or ''}@{host or ''}:{port or ''}/{db or ''}"

def _mask_secret(value: str | None, *, keep_start: int = 3, keep_end: int = 2) -> str:
    if not value:
        return ""
    raw = str(value)
    if len(raw) <= keep_start + keep_end:
        return "*" * len(raw)
    return f"{raw[:keep_start]}{'*' * (len(raw) - keep_start - keep_end)}{raw[-keep_end:]}"


def config_health_snapshot() -> dict:
    """輸出可安全落 log 的設定摘要（不含明碼敏感資訊）。"""
    return {
        "env": ENV,
        "langfuse_tracing_environment": LANGFUSE_TRACING_ENVIRONMENT,
        "env_file": str(ENV_FILE),
        "backend": {"ip": BACKEND_IP, "port": BACKEND_PORT},
        "paths": {
            "log_path": LOG_PATH,
            "rag_index_dir": rag_index_dir,
            "rag_input_dir": rag_input_dir,
        },
        "providers": {
            "langfuse_configured": LANGFUSE_CONFIGURED,
            "vllm_api_key_present": bool(api_key),
            "hf_token_present": bool(hf_token),
            "mysql_configured": bool(MYSQL_IP and MYSQL_USER and MYSQL_PORT and MYSQL_NAME),
            "mongo_configured": bool(MONGO_IP and MONGO_PORT and MONGO_USER),
            "redis_configured": bool(REDIS_IP),
            "ollama_configured": bool(CHAT_OLLAMA_IP and OLLAMA_PORT),
            "es_configured": bool(ES_IP and ES_PORT),
        },
        "masked": {
            "api_key": _mask_secret(api_key, keep_start=4, keep_end=4),
            "hf_token": _mask_secret(hf_token, keep_start=4, keep_end=4),
            "mongo_uri": _mask_secret(MONGO_URI, keep_start=10, keep_end=6),
        },
    }


ENV = _normalize_env(os.getenv("ENV"))
ENV_FILE = _load_env_file(ENV)

# Langfuse trace-level environment (UI filter), not the same as metadata-only fields.
os.environ.setdefault("LANGFUSE_TRACING_ENVIRONMENT", ENV)
LANGFUSE_TRACING_ENVIRONMENT = os.environ["LANGFUSE_TRACING_ENVIRONMENT"]


def _strip_langfuse_credential(value: str | None) -> str:
    return (value or "").strip().strip('"')


LANGFUSE_PUBLIC_KEY = _strip_langfuse_credential(_getenv("LANGFUSE_PUBLIC_KEY"))
LANGFUSE_SECRET_KEY = _strip_langfuse_credential(_getenv("LANGFUSE_SECRET_KEY"))
LANGFUSE_BASE_URL = _getenv("LANGFUSE_BASE_URL")
LANGFUSE_CONFIGURED = bool(LANGFUSE_PUBLIC_KEY and LANGFUSE_SECRET_KEY)
if not LANGFUSE_CONFIGURED:
    os.environ["LANGFUSE_TRACING_ENABLED"] = "false"

JWT_SECRET = _getenv("JWT_SECRET", "change-me-in-production-min-32-chars-long!!")
JWT_ALGORITHM = "HS256"
JWT_EXPIRE_MINUTES = _getenv_int("JWT_EXPIRE_MINUTES", 60 * 24 * 7)
ENCRYPTION_KEY = _getenv("ENCRYPTION_KEY", "")

HTTP_PROXY = _getenv("http_proxy_")
HTTPS_PROXY = _getenv("https_proxy_")

RABBITMQ_URL = _getenv("RABBITMQ_URL")
QUEUE_NAME = _getenv("QUEUE_NAME")

MYSQL_IP = _getenv("MYSQL_IP")
MYSQL_USER = _getenv("MYSQL_USER")
MYSQL_PASSWORD = _getenv("MYSQL_PASSWORD")
MYSQL_PORT = _getenv("MYSQL_PORT")
MYSQL_NAME = _getenv("MYSQL_NAME")
MYSQL_URL = _safe_join_db_url(MYSQL_USER, MYSQL_PASSWORD, MYSQL_IP, MYSQL_PORT, MYSQL_NAME)

MONGO_IP = _getenv("MONGO_IP")
MONGO_PORT = _getenv("MONGO_PORT")
MONGO_USER = _getenv("MONGO_USER")
MONGO_PASSWORD = _getenv("MONGO_PASSWORD")
MONGO_URI = f"mongodb://{MONGO_USER or ''}:{MONGO_PASSWORD or ''}@{MONGO_IP or ''}:{MONGO_PORT or ''}"

CHAT_OLLAMA_IP = _getenv("CHAT_OLLAMA_IP")
RAG_OLLAMA_IP = _getenv("RAG_OLLAMA_IP")
OLLAMA_PORT = _getenv("OLLAMA_PORT")
REDIS_IP = _getenv("REDIS_IP")
QDRANT_IP = _getenv("QDRANT_IP")
QDRANT_PORT = _getenv("QDRANT_API_PORT")
ES_IP = _getenv("ES_IP")
ES_PORT = _getenv("ES_PORT")

CONFIG_PATH = get_config_dir()
config = configparser.ConfigParser()
config.read(CONFIG_PATH, encoding="utf-8")

project_root = "/app"
LOG_PATH = _getenv("LOG_PATH", config.get("log_setting", "LOG_PATH", fallback="/app/db/logs"))

BACKEND_IP = _getenv("BACKEND_IP", "127.0.0.1")
BACKEND_PORT = _getenv_int("BACKEND_PORT", 44000)
backend_cache_days = config.getint("backend_setting", "cache_days", fallback=90)
backend_version = config.get("backend_setting", "version", fallback="v1")

def _strip_api_key(value: str | None) -> str | None:
    cleaned = _strip_langfuse_credential(value)
    return cleaned if cleaned else None


api_key = _strip_api_key(_getenv("OPENAI_API_KEY")) or _strip_api_key(_getenv("API_KEY"))
hf_token = config.get("LLM", "hf_token", fallback="")
time_out_second = config.getint("LLM", "time_out_second", fallback=120)

chat_model_enable = config.getboolean("chat_setting", "enable", fallback=True)
chat_model_cloud = config.getboolean("chat_setting", "cloud", fallback=False)
chat_model_name = config.get("chat_setting", "model", fallback="deepseek-r1:7b")
chat_model_temperature = config.getfloat("chat_setting", "temperature", fallback=0.7)
chat_model_top_k = config.getint("chat_setting", "top_k", fallback=50)
chat_model_top_p = config.getfloat("chat_setting", "top_p", fallback=0.95)
chat_model_do_sample = config.getboolean("chat_setting", "do_sample", fallback=True)

rag_model_enable = config.getboolean("rag_setting", "enable", fallback=True)
rag_model_cloud = config.getboolean("rag_setting", "cloud", fallback=False)
rag_model_name = config.get("rag_setting", "rag_model_name", fallback="bge-m3:latest")
rag_dim = config.getint("rag_setting", "rag_dim", fallback=1024)
rerank_model_name = config.get("rag_setting", "rerank_model", fallback="")
reranker_top_k = config.getint("rag_setting", "reranker_top_k", fallback=10)
retrivel_top_k = config.getint("rag_setting", "retrivel_top_k", fallback=10)
rag_index_dir = config.get("rag_setting", "index_dir", fallback="/app/db/rag_db")
rag_input_dir = config.get("rag_setting", "input_dir", fallback="/app/rag_doc")
rag_folder_path = config.get("rag_setting", "folder_path", fallback="/app/rag_doc")
rag_images_path = config.get("rag_setting", "images_path", fallback="/app/db/images")
retrive_mode = config.get("rag_setting", "retrive_mode", fallback="vector")
rag_chunk_size = config.getint("rag_setting", "rag_chunk_size", fallback=1024)
rag_start_time = config.get("rag_setting", "rag_start_time", fallback="")
rag_end_time = config.get("rag_setting", "rag_end_time", fallback="")
INDEX_NAME = config.get("rag_setting", "rag_index_name", fallback="iap")

platform_name = config.get("project_setting", "platform_name", fallback=PLATFORM_NAME)
platform_slug = config.get("project_setting", "platform_slug", fallback=PLATFORM_SLUG)

etl_inbox_root = config.get("etl_inbox", "root", fallback=f"{rag_input_dir}/inbox")
etl_inbox_with_embedding = config.get("etl_inbox", "with_embedding", fallback="with_embedding")
etl_inbox_without_embedding = config.get(
    "etl_inbox", "without_embedding", fallback="without_embedding"
)

pd_instr_model_enable = config.getboolean("pd_instr_setting", "enable", fallback=False)
pd_instr_model_cloud = config.getboolean("pd_instr_setting", "cloud", fallback=False)
pd_instr_model_name = config.get("pd_instr_setting", "model", fallback="deepseek-r1:7b")
pd_instr_model_temperature = config.getfloat("pd_instr_setting", "temperature", fallback=0.3)
pd_instr_model_top_k = config.getint("pd_instr_setting", "top_k", fallback=10)
pd_instr_model_top_p = config.getfloat("pd_instr_setting", "top_p", fallback=0.2)
pd_instr_model_do_sample = config.getboolean("pd_instr_setting", "do_sample", fallback=True)

frontend_show_pic = config.getboolean("frontend_setting", "show_pic", fallback=True)
frontend_cache_days = config.getint("frontend_setting", "cache_days", fallback=90)
login_enable = config.getboolean("auth", "login_enable", fallback=True)

class rag_url:
    clear_index = f"http://{BACKEND_IP}:{BACKEND_PORT}/clear_index"
    get_index = f"http://{BACKEND_IP}:{BACKEND_PORT}/get_index"
    get_response = f"http://{BACKEND_IP}:{BACKEND_PORT}/get_response"
    retrieve_chunk = f"http://{BACKEND_IP}:{BACKEND_PORT}/retrieve_chunk"
    build_node = f"http://{BACKEND_IP}:{BACKEND_PORT}/build_node"
    get_list_files = f"http://{BACKEND_IP}:{BACKEND_PORT}/get_list_files"
    build_image = f"http://{BACKEND_IP}:{BACKEND_PORT}/build_image"
    get_image = f"http://{BACKEND_IP}:{BACKEND_PORT}/get_image"

_API_V1 = f"http://{BACKEND_IP}:{BACKEND_PORT}/api/v1"


class common_url:
    check_backend = f"{_API_V1}/common/check_backend"
    translate_zh2vi = f"{_API_V1}/common/translate_zh2vi"
    translate = f"{_API_V1}/common/translate"
    web_search = f"{_API_V1}/common/web_search"


class auth_api_url:
    register = f"{_API_V1}/auth/register"
    login = f"{_API_V1}/auth/login"
    me = f"{_API_V1}/auth/me"


class notebook_api_url:
    chat = f"{_API_V1}/notebook/chat/retrieve"
    chat_stream = f"{_API_V1}/notebook/chat/stream"
    history_session_ids = f"{_API_V1}/notebook/history/session_ids"
    history_session = f"{_API_V1}/notebook/history/session"
    delete_session = f"{_API_V1}/notebook/history/session"
    documents_upload = f"{_API_V1}/notebook/documents/upload"
    documents_list = f"{_API_V1}/notebook/documents"
    documents_delete = f"{_API_V1}/notebook/documents"
    job_status = f"{_API_V1}/notebook/jobs"
    job_pause = f"{_API_V1}/notebook/jobs/{{job_id}}/pause"
    job_cancel = f"{_API_V1}/notebook/jobs/{{job_id}}/cancel"
    job_resume = f"{_API_V1}/notebook/jobs/{{job_id}}/resume"


class account_api_url:
    profile = f"{_API_V1}/account/profile"
    api_key = f"{_API_V1}/account/api-key"
    usage_summary = f"{_API_V1}/account/usage/summary"
    usage_daily = f"{_API_V1}/account/usage/daily"
    usage_by_model = f"{_API_V1}/account/usage/by-model"
    