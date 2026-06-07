import sys, os
sys.path.extend(['.','..'])
from dotenv import load_dotenv
import os
import configparser
from common.utils import get_config_dir

ENV = os.getenv('ENV', 'prod')
if ENV == 'prod':
    load_dotenv(dotenv_path="/app/configs/.env.prod")
else:
    load_dotenv(dotenv_path="/app/configs/.env.dev")
####################### database conected info ##################################
# RABBITMQ
RABBITMQ_URL = os.getenv('RABBITMQ_URL')
QUEUE_NAME = os.getenv('QUEUE_NAME')

# Mysql
DATABASE_IP = os.getenv('DATABASE_IP')
DATABASE_USER = os.getenv('DATABASE_USER')
DATABASE_PASSWORD = os.getenv('DATABASE_PASSWORD')
DATABASE_PORT = os.getenv('DATABASE_PORT')
DATABASE_NAME = os.getenv('DATABASE_NAME')
DATABASE_URL=f"mysql+pymysql://{DATABASE_USER}:{DATABASE_PASSWORD}@{DATABASE_IP}:{DATABASE_PORT}/{DATABASE_NAME}"
# Mongo db
MONGO_IP = os.getenv('MONGO_IP')
MONGO_PORT = os.getenv('MONGO_PORT')
MONGO_USER = os.getenv('MONGO_USER')
MONGO_PASSWORD = os.getenv('MONGO_PASSWORD')
##########################################################################3

CONFIG_PATH = get_config_dir()
config = configparser.ConfigParser()
config.read(CONFIG_PATH)

BACKEND_IP = os.getenv("BACKEND_IP") or "iap_backend"
BACKEND_PORT = int(os.getenv("BACKEND_PORT") or 44000)
# Redis
redis_name = f"{config.get('redis', 'name', fallback='llm_redis')}_{ENV}"
# Backend setting
backend_cache_days = config.getint("backend_setting", "cache_days", fallback=90)
backend_version = config.get("backend_setting", "version", fallback="v1")
# Project setting
project_root = "/app"
LOG_PATH = config.get("log_setting", "LOG_PATH", fallback="/app/db/logs")
# LLM setting
api_key = config.get("LLM", "api_key", fallback="")
hf_token = config.get("LLM", "hf_token", fallback="")
time_out_second = config.getint("LLM", "time_out_second", fallback=120)

frontend_cache_days = 90
frontend_show_pic = False
login_mock = config.getboolean("auth", "login_mock", fallback=False)
demo_employee_id = config.get("auth", "demo_employee_id", fallback="10038437")
demo_department = config.get("auth", "demo_department", fallback="生成式人工智慧研發部")
demo_display_name = config.get("auth", "demo_display_name", fallback="張翊翔 (Sean Chang)")
# API（路徑與 fast_api_service 掛載一致）
_API = f"http://{BACKEND_IP}:{BACKEND_PORT}/api/v1"


class common_url:
    check_backend = f"{_API}/common/check_backend"
    translate_zh2vi = f"{_API}/common/translate_zh2vi"
    translate = f"{_API}/common/translate"


class auth_api_url:
    register = f"{_API}/auth/register"
    login = f"{_API}/auth/login"
    me = f"{_API}/auth/me"


class notebook_api_url:
    chat = f"{_API}/notebook/chat/retrieve"
    chat_stream = f"{_API}/notebook/chat/stream"
    history_session_ids = f"{_API}/notebook/history/session_ids"
    history_session = f"{_API}/notebook/history/session"
    delete_session = f"{_API}/notebook/history/session"
    documents_upload = f"{_API}/notebook/documents/upload"
    documents_list = f"{_API}/notebook/documents"
    documents_delete = f"{_API}/notebook/documents"
    job_status = f"{_API}/notebook/jobs"
    job_pause = f"{_API}/notebook/jobs/{{job_id}}/pause"
    job_cancel = f"{_API}/notebook/jobs/{{job_id}}/cancel"
    job_resume = f"{_API}/notebook/jobs/{{job_id}}/resume"


class account_api_url:
    profile = f"{_API}/account/profile"
    api_key = f"{_API}/account/api-key"
    usage_summary = f"{_API}/account/usage/summary"
    usage_daily = f"{_API}/account/usage/daily"
    usage_by_model = f"{_API}/account/usage/by-model"