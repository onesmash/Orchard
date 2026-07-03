from __future__ import annotations

import logging
import os
from contextlib import suppress
from logging.handlers import TimedRotatingFileHandler
from pathlib import Path

_LOGGER_NAME = "orchard"
_FILE_HANDLER_NAME = "orchard-file"
_STREAM_HANDLER_NAME = "orchard-stream"
_LOG_LEVELS = {
    "error": logging.ERROR,
    "warning": logging.WARNING,
    "warn": logging.WARNING,
    "info": logging.INFO,
    "debug": logging.DEBUG,
    "trace": logging.DEBUG,
}


def orchard_log_dir() -> Path:
    return Path.home() / ".orchard" / "logs"


def orchard_log_path() -> Path:
    return orchard_log_dir() / "orchard.log"


def orchard_log_level() -> int:
    raw = os.environ.get("ORCHARD_LOG_LEVEL", "info").strip().lower()
    return _LOG_LEVELS.get(raw, logging.INFO)


def configure_orchard_logger(*, console: bool = False, force: bool = False) -> logging.Logger:
    logger = logging.getLogger(_LOGGER_NAME)
    logger.setLevel(logging.DEBUG)
    logger.propagate = False

    if force:
        for handler in list(logger.handlers):
            logger.removeHandler(handler)
            with suppress(Exception):
                handler.close()

    formatter = logging.Formatter(
        fmt="%(asctime)s %(levelname)s %(name)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S",
    )

    if not any(getattr(handler, "name", "") == _FILE_HANDLER_NAME for handler in logger.handlers):
        log_dir = orchard_log_dir()
        log_dir.mkdir(parents=True, exist_ok=True)
        file_handler = TimedRotatingFileHandler(
            orchard_log_path(),
            when="midnight",
            backupCount=14,
            encoding="utf-8",
        )
        file_handler.name = _FILE_HANDLER_NAME
        file_handler.setLevel(logging.DEBUG)
        file_handler.setFormatter(formatter)
        logger.addHandler(file_handler)

    if console and not any(getattr(handler, "name", "") == _STREAM_HANDLER_NAME for handler in logger.handlers):
        stream_handler = logging.StreamHandler()
        stream_handler.name = _STREAM_HANDLER_NAME
        stream_handler.setLevel(logging.DEBUG)
        stream_handler.setFormatter(formatter)
        logger.addHandler(stream_handler)

    return logger


def get_orchard_logger(name: str, *, console: bool = False, force: bool = False) -> logging.Logger:
    configure_orchard_logger(console=console, force=force)
    logger = logging.getLogger(f"{_LOGGER_NAME}.{name}")
    logger.setLevel(logging.DEBUG)
    logger.propagate = True
    return logger
