# coding:utf-8
import sys
import os
import logging
import traceback
from logging.handlers import TimedRotatingFileHandler
from configs.config import LOG_PATH, ENV, config_health_snapshot


# ========= 自訂 Formatter =========
class CustomFormatter(logging.Formatter):
    """彩色輸出到 console"""
    grey = "\x1b[38;20m"
    yellow = "\x1b[33;20m"
    red = "\x1b[31;20m"
    bold_red = "\x1b[31;1m"
    reset = "\x1b[0m"
    format_str = "%(asctime)s %(levelname)s: %(message)s (%(filename)s:%(lineno)d)"

    FORMATS = {
        logging.DEBUG: grey + format_str + reset,
        logging.INFO: grey + format_str + reset,
        logging.WARNING: yellow + format_str + reset,
        logging.ERROR: red + format_str + reset,
        logging.CRITICAL: bold_red + format_str + reset,
    }

    def format(self, record):
        log_fmt = self.FORMATS.get(record.levelno)
        formatter = logging.Formatter(log_fmt, "%Y-%m-%d %H:%M:%S")
        return formatter.format(record)


class StacktraceFormatter(logging.Formatter):
    """在 DEBUG 且包含 'open file' 時，額外輸出 stacktrace"""
    def format(self, record):
        s = super().format(record)
        if record.levelno == logging.DEBUG and "open file" in record.getMessage():
            stack = []
            for line in traceback.format_stack():
                if "site-packages" not in line:  # ✅ 排除掉第三方套件
                    stack.append(line)
            if stack:
                s += "\n" + "".join(stack)
        return s


def _resolve_log_level() -> int:
    raw_level = os.getenv("LOG_LEVEL", "INFO").strip().upper()
    return getattr(logging, raw_level, logging.INFO)


def _is_tty_stream(stream) -> bool:
    return hasattr(stream, "isatty") and stream.isatty()


# ========= 初始化 logging =========
def init_logging(filename_prefix="app"):
    """初始化 logging 系統"""
    os.makedirs(LOG_PATH, exist_ok=True)
    log_level = _resolve_log_level()

    # root logger
    root = logging.getLogger()
    if getattr(root, "_faca_logging_initialized", False):
        for handler in root.handlers:
            handler.setLevel(log_level)
        root.setLevel(log_level)
        return
    root.handlers = []
    root.setLevel(log_level)

    # ===== 過濾第三方套件 =====
    for name in logging.root.manager.loggerDict:
        if not name.startswith("uvicorn"):
            # 非專案、非 uvicorn 的 logger → 靜音
            logging.getLogger(name).setLevel(logging.WARNING)
            logging.getLogger(name).propagate = False

    # uvicorn log 要跟自訂的一起寫
    logging.getLogger("uvicorn").setLevel(log_level)
    logging.getLogger("uvicorn").propagate = True
    logging.getLogger("uvicorn.error").propagate = True
    logging.getLogger("uvicorn.access").propagate = True

    # ===== Log 檔案設定（每天換檔，以日期命名） =====
    log_file = os.path.join(LOG_PATH, f"{filename_prefix}.log")
    fileHandler = TimedRotatingFileHandler(
        log_file,
        when="midnight", interval=1, backupCount=14, encoding="utf-8"
    )
    fileHandler.suffix = "%Y-%m-%d"  # 檔名後加日期
    fileHandler.setFormatter(StacktraceFormatter(
        "%(asctime)s %(levelname)s: %(message)s (%(filename)s:%(lineno)d)",
        "%Y-%m-%d %H:%M:%S"
    ))
    fileHandler.setLevel(log_level)
    root.addHandler(fileHandler)

    # ===== Console 設定（彩色輸出） =====
    consoleHandler = logging.StreamHandler(sys.stdout)
    if _is_tty_stream(sys.stdout):
        consoleHandler.setFormatter(CustomFormatter())
    else:
        consoleHandler.setFormatter(logging.Formatter(
            "%(asctime)s %(levelname)s: %(message)s (%(filename)s:%(lineno)d)",
            "%Y-%m-%d %H:%M:%S",
        ))
    consoleHandler.setLevel(log_level)
    root.addHandler(consoleHandler)

    root._faca_logging_initialized = True
    logging.info("Logging initialized env=%s level=%s path=%s", ENV, logging.getLevelName(log_level), LOG_PATH)
    try:
        logging.info("Config health snapshot: %s", config_health_snapshot())
    except Exception as exc:
        logging.warning("Config health snapshot failed: %s", exc)
