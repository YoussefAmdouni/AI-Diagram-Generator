"""
Shared logger 

"""
import os
import json
import logging
import logging.handlers
from datetime import datetime, timezone

from context import request_id_var

# ─── JSON formatter ───────────────────────────────────────────────────────────

class JSONFormatter(logging.Formatter):
    """Emit each log record as a single JSON line."""

    def format(self, record: logging.LogRecord) -> str:
        log_obj = {
            "timestamp": datetime.fromtimestamp(record.created, tz=timezone.utc).isoformat(),
            "level":     record.levelname,
            "logger":    record.name,
            "message":   record.getMessage(),
            "module":    record.module,
            "line":      record.lineno,
            "request_id": request_id_var.get(),
        }
        if record.exc_info:
            log_obj["exception"] = self.formatException(record.exc_info)

        # Include any extra fields passed via extra={} in log calls
        _SKIP = {
            "msg", "args", "levelname", "levelno", "pathname", "filename",
            "module", "exc_info", "exc_text", "stack_info", "lineno",
            "funcName", "created", "msecs", "relativeCreated", "thread",
            "threadName", "processName", "process", "name", "message",
        }
        for key, val in record.__dict__.items():
            if key not in _SKIP:
                log_obj[key] = val

        return json.dumps(log_obj)


# ─── Handlers (configured once at import time) ────────────────────────────────

LOGS_DIR = "agent_logs"
os.makedirs(LOGS_DIR, exist_ok=True)

_rotating_handler = logging.handlers.RotatingFileHandler(
    filename=os.path.join(LOGS_DIR, "agent.log"),
    maxBytes=10 * 1024 * 1024,   # 10 MB
    backupCount=5,
    encoding="utf-8",
)
_rotating_handler.setFormatter(JSONFormatter())

_console_handler = logging.StreamHandler()
_console_handler.setFormatter(JSONFormatter())

logging.basicConfig(level=logging.INFO, handlers=[_rotating_handler, _console_handler])


# ─── Public helper ────────────────────────────────────────────────────────────

def get_logger(name: str) -> logging.Logger:
    """Return a named logger that uses the shared JSON handlers."""
    return logging.getLogger(name)