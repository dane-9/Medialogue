import json
import logging
import sys
from collections import deque
from datetime import datetime, timezone


#: Attributes every LogRecord has; anything else was passed with ``extra=``.
_STANDARD_ATTRIBUTES = frozenset(vars(logging.LogRecord("", 0, "", 0, "", (), None))) | {"message", "asctime", "taskName"}


def record_payload(record: logging.LogRecord, formatter: logging.Formatter | None = None) -> dict:
    """One log line as data: time, level, logger, message, any extra fields, exception."""

    payload = {
        "timestamp": datetime.fromtimestamp(record.created, timezone.utc).isoformat(),
        "level": record.levelname,
        "logger": record.name,
        "message": record.getMessage(),
    }
    for key, value in vars(record).items():
        if key not in _STANDARD_ATTRIBUTES and not key.startswith("_"):
            payload[key] = value
    if record.exc_info:
        payload["exception"] = (formatter or logging.Formatter()).formatException(record.exc_info)
    return payload


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        return json.dumps(record_payload(record, self), default=str)


class RecentLogs(logging.Handler):
    """The latest log lines, kept in memory so the API can show them (no Docker access needed)."""

    def __init__(self, capacity: int = 2000) -> None:
        super().__init__()
        self.lines: deque[dict] = deque(maxlen=capacity)

    def emit(self, record: logging.LogRecord) -> None:
        try:
            self.lines.append(json.loads(json.dumps(record_payload(record), default=str)))
        except Exception:
            self.handleError(record)


recent_logs = RecentLogs()


def configure_logging(level: str = "INFO") -> None:
    root = logging.getLogger()
    root.setLevel(getattr(logging, level.upper(), logging.INFO))
    if not root.handlers:
        handler = logging.StreamHandler(sys.stdout)
        handler.setFormatter(JsonFormatter())
        root.addHandler(handler)
    if recent_logs not in root.handlers:
        root.addHandler(recent_logs)
    # Uvicorn's error logger (unhandled request errors) does not propagate.
    uvicorn_errors = logging.getLogger("uvicorn.error")
    if recent_logs not in uvicorn_errors.handlers:
        uvicorn_errors.addHandler(recent_logs)
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
