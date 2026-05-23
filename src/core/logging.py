"""Structured logging configuration using structlog."""

from __future__ import annotations

import json
import logging
import re
import sys
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path
from typing import Any, TextIO

import structlog

from src.core.config import settings

ANSI_ESCAPE_RE = re.compile(r"\x1b\[[0-?]*[ -/]*[@-~]")
MAX_FILE_VALUE_LENGTH = 1200


def _json_dumps(data: Any, **kwargs: Any) -> str:
    kwargs.setdefault("ensure_ascii", False)
    kwargs.setdefault("default", str)
    return json.dumps(data, **kwargs)


def _clean_file_value(value: Any) -> Any:
    if not isinstance(value, str):
        return value

    value = ANSI_ESCAPE_RE.sub("", value)
    if len(value) > MAX_FILE_VALUE_LENGTH:
        return f"{value[:MAX_FILE_VALUE_LENGTH]}...<truncated>"
    return value


def _prepare_file_event(
    _logger: logging.Logger,
    _method_name: str,
    event_dict: structlog.typing.EventDict,
) -> structlog.typing.EventDict:
    """Keep file logs compact, UTF-8 clean, and single-line JSON friendly."""
    exc_info = event_dict.pop("exc_info", None)
    if exc_info:
        if isinstance(exc_info, tuple) and len(exc_info) >= 2 and exc_info[1] is not None:
            event_dict["exception_type"] = type(exc_info[1]).__name__
            event_dict["error"] = str(exc_info[1])
        else:
            event_dict["exception"] = True

    for noisy_key in ("traceback", "stack", "stack_info"):
        if noisy_key in event_dict:
            event_dict[f"{noisy_key}_omitted"] = True
            event_dict.pop(noisy_key, None)

    return {key: _clean_file_value(value) for key, value in event_dict.items()}


def _create_daily_file_handler(log_path: Path) -> logging.Handler:
    log_path.parent.mkdir(parents=True, exist_ok=True)
    handler = TimedRotatingFileHandler(
        log_path,
        when="midnight",
        interval=1,
        backupCount=settings.log_backup_count,
        encoding="utf-8",
        utc=True,
    )
    handler.suffix = "%Y-%m-%d"
    return handler


def _compact_console_exception(
    sio: TextIO,
    exc_info: tuple[type[BaseException], BaseException, Any],
) -> None:
    exc_type, exc, _tb = exc_info
    sio.write(f"\n{exc_type.__name__}: {exc}\n")


def setup_logging() -> None:
    """Configure structlog: stdout + compact UTC daily-rotated file logging."""
    shared_processors: list = [
        structlog.contextvars.merge_contextvars,
        structlog.stdlib.add_log_level,
        structlog.stdlib.add_logger_name,
        structlog.processors.TimeStamper(fmt="iso", utc=True),
        structlog.processors.StackInfoRenderer(),
    ]

    if settings.app_env == "development":
        console_renderer = structlog.dev.ConsoleRenderer(
            colors=True,
            exception_formatter=_compact_console_exception,
        )
    else:
        console_renderer = structlog.processors.JSONRenderer()

    file_renderer = structlog.processors.JSONRenderer(serializer=_json_dumps)

    structlog.configure(
        processors=[
            *shared_processors,
            structlog.stdlib.ProcessorFormatter.wrap_for_formatter,
        ],
        logger_factory=structlog.stdlib.LoggerFactory(),
        wrapper_class=structlog.stdlib.BoundLogger,
        cache_logger_on_first_use=True,
    )

    console_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            console_renderer,
        ],
    )

    file_formatter = structlog.stdlib.ProcessorFormatter(
        foreign_pre_chain=shared_processors,
        processors=[
            _prepare_file_event,
            structlog.stdlib.ProcessorFormatter.remove_processors_meta,
            file_renderer,
        ],
    )

    handlers: list[logging.Handler] = []

    console_handler = logging.StreamHandler(sys.stdout)
    console_handler.setFormatter(console_formatter)
    handlers.append(console_handler)

    if settings.log_file:
        log_path = Path(settings.log_file)
        file_handler = _create_daily_file_handler(log_path)
        file_handler.setFormatter(file_formatter)
        handlers.append(file_handler)

    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    for handler in handlers:
        root_logger.addHandler(handler)
    root_logger.setLevel(getattr(logging, settings.log_level.upper(), logging.INFO))

    for logger_name in ("uvicorn", "uvicorn.access", "uvicorn.error"):
        uv_logger = logging.getLogger(logger_name)
        uv_logger.handlers.clear()
        uv_logger.propagate = True

    logging.getLogger("uvicorn.access").setLevel(logging.INFO)
    logging.getLogger("uvicorn.error").setLevel(logging.WARNING)
    logging.getLogger("sqlalchemy.engine").setLevel(logging.WARNING)


def get_logger(name: str) -> structlog.stdlib.BoundLogger:
    """Get a structured logger instance."""
    return structlog.get_logger(name)
