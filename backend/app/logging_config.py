import datetime
import logging
import os
import re
import sys
from collections import deque
from collections.abc import Callable

# Patterns to mask sensitive tokens, credentials, and API keys
_REDACTION_RULES: list[tuple[re.Pattern[str], str]] = [
    # Bearer tokens
    (re.compile(r"(Bearer\s+)[A-Za-z0-9_\-\.]{10,}", re.IGNORECASE), r"\1[REDACTED]"),
    # Google API keys (typically AIzaSy...)
    (re.compile(r"AIza[0-9A-Za-z\-_]{30,40}"), "[REDACTED_GEMINI_KEY]"),
    # Anthropic API keys (sk-ant-...)
    (re.compile(r"sk-ant-[A-Za-z0-9_\-]{20,}"), "[REDACTED_CLAUDE_KEY]"),
    # OpenAI API keys (sk-...)
    (re.compile(r"sk-(?:proj-)?[A-Za-z0-9_\-]{20,}"), "[REDACTED_OPENAI_KEY]"),
    # Key-value secret fields in JSON / query / headers
    (
        re.compile(
            r"((?:['\"]?(?:api[_-]?key|secret|password|auth[_-]?token|access[_-]?token|x-goog-api-key)['\"]?)"
            r"\s*[:=]\s*['\"]?)[A-Za-z0-9_\-\.]{8,}(['\"]?)",
            re.IGNORECASE,
        ),
        r"\1[REDACTED]\2",
    ),
    # Query parameters containing keys
    (re.compile(r"([?&](?:key|api_key|token|auth)=)[^&\s]+", re.IGNORECASE), r"\1[REDACTED]"),
]


def sanitize_sensitive_data(message: str) -> str:
    """Mask secrets, tokens, and API credentials from log messages."""
    if not message:
        return message
    sanitized = message
    for pattern, replacement in _REDACTION_RULES:
        sanitized = pattern.sub(replacement, sanitized)
    return sanitized


_ANSI_COLORS = {
    logging.DEBUG: "\033[36m",  # Cyan
    logging.INFO: "\033[32m",  # Green
    logging.WARNING: "\033[33m",  # Yellow
    logging.ERROR: "\033[31m",  # Red
    logging.CRITICAL: "\033[1;31m",  # Bold Red
}
_ANSI_RESET = "\033[0m"


class StructuredFormatter(logging.Formatter):
    """Clean ISO-timestamped log formatter with credential redaction and optional ANSI colors."""

    def __init__(self, use_colors: bool = False) -> None:
        super().__init__()
        self.use_colors = use_colors

    def formatTime(self, record: logging.LogRecord, datefmt: str | None = None) -> str:
        dt = datetime.datetime.fromtimestamp(record.created).astimezone()
        return dt.isoformat(timespec="seconds")

    def format(self, record: logging.LogRecord) -> str:
        timestamp = self.formatTime(record)
        level_name = record.levelname
        name = record.name

        msg = record.getMessage()
        msg = sanitize_sensitive_data(msg)

        if self.use_colors and record.levelno in _ANSI_COLORS:
            color = _ANSI_COLORS[record.levelno]
            level_str = f"{color}{level_name:<5}{_ANSI_RESET}"
        else:
            level_str = f"{level_name:<5}"

        output = f"{timestamp} | {level_str} | {name} | {msg}"

        if record.exc_info:
            if not record.exc_text:
                record.exc_text = self.formatException(record.exc_info)
            if record.exc_text:
                output = f"{output}\n{sanitize_sensitive_data(record.exc_text)}"

        return output


def _should_use_colors(explicit_flag: bool | None = None) -> bool:
    if explicit_flag is not None:
        return explicit_flag
    if os.environ.get("NO_COLOR"):
        return False
    return hasattr(sys.stdout, "isatty") and sys.stdout.isatty()


_MEMORY_LOG_BUFFER: deque[dict[str, str]] = deque(maxlen=1000)
_EXTERNAL_LOG_SINK: Callable[[dict[str, str]], None] | None = None


class CircularMemoryLogHandler(logging.Handler):
    """Stores recent log entries in an in-memory ring buffer with a pluggable external sink."""

    def emit(self, record: logging.LogRecord) -> None:
        try:
            dt = datetime.datetime.fromtimestamp(record.created).astimezone()
            timestamp = dt.isoformat(timespec="seconds")
            msg = sanitize_sensitive_data(record.getMessage())
            entry = {
                "timestamp": timestamp,
                "level": record.levelname,
                "logger": record.name,
                "message": msg,
            }
            _MEMORY_LOG_BUFFER.append(entry)
            if _EXTERNAL_LOG_SINK is not None:
                try:
                    _EXTERNAL_LOG_SINK(entry)
                except Exception:
                    pass
        except Exception:
            self.handleError(record)


def get_runtime_logs(limit: int = 150) -> list[dict[str, str]]:
    """Return the most recent sanitized runtime log entries."""
    clamped = max(1, min(limit, 1000))
    return list(_MEMORY_LOG_BUFFER)[-clamped:]


def set_external_log_sink(sink: Callable[[dict[str, str]], None] | None) -> None:
    """Set or remove a redirect hook for distributing logs to a local or remote server."""
    global _EXTERNAL_LOG_SINK
    _EXTERNAL_LOG_SINK = sink


def setup_logging(
    level: int = logging.INFO,
    use_colors: bool | None = None,
) -> logging.Logger:
    """Initialize root and application loggers with structured formatting, redaction, and ring buffer."""
    root_logger = logging.getLogger()
    root_logger.setLevel(level)

    # Avoid adding duplicate handlers if setup_logging is called multiple times
    for handler in list(root_logger.handlers):
        if getattr(handler, "_careerforge_structured", False):
            return root_logger

    handler = logging.StreamHandler(sys.stdout)
    handler._careerforge_structured = True  # type: ignore[attr-defined]
    handler.setFormatter(StructuredFormatter(use_colors=_should_use_colors(use_colors)))
    root_logger.addHandler(handler)

    mem_handler = CircularMemoryLogHandler()
    mem_handler._careerforge_structured = True  # type: ignore[attr-defined]
    root_logger.addHandler(mem_handler)

    # Silence overly verbose external libraries
    logging.getLogger("httpx").setLevel(logging.WARNING)
    logging.getLogger("httpcore").setLevel(logging.WARNING)
    logging.getLogger("uvicorn.access").setLevel(logging.WARNING)

    return root_logger


def get_logger(name: str) -> logging.Logger:
    """Return a logger configured under the application logging hierarchy."""
    return logging.getLogger(name)
