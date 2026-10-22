"""Rotating file log — the app runs under pythonw.exe, which has no console.

Without this, an exception in a worker thread or a rejected analysis leaves no
trace anywhere, and the only symptom the user sees is "拖进去没反应".  One
1 MB × 3 rotation in the data dir is enough for postmortems and costs nothing.
"""
from __future__ import annotations

import logging
import os
from logging.handlers import RotatingFileHandler

_configured_for: str | None = None


def setup(data_dir: str, level: int = logging.INFO) -> logging.Logger:
    """Idempotent per data dir; returns the app logger."""
    global _configured_for
    logger = logging.getLogger("agenttodo")
    if _configured_for == data_dir:
        return logger
    path = os.path.join(data_dir, "app.log")
    try:
        os.makedirs(data_dir, exist_ok=True)
        handler = RotatingFileHandler(path, maxBytes=1_000_000, backupCount=3,
                                      encoding="utf-8")
    except OSError:
        return logger
    handler.setFormatter(logging.Formatter(
        "%(asctime)s %(levelname)-7s %(threadName)s %(message)s",
        datefmt="%Y-%m-%d %H:%M:%S"))
    logger.handlers = [handler]
    logger.setLevel(level)
    logger.propagate = False
    _configured_for = data_dir
    return logger


def get() -> logging.Logger:
    return logging.getLogger("agenttodo")
