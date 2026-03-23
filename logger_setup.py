"""
logger_setup.py — Configures the root logger for the bot.

Writes to both a rotating file and (optionally) stdout.
Import and call `setup_logging()` once at program start.
"""

import logging
import logging.handlers
from config import LOG_PATH


def setup_logging(level: int = logging.INFO) -> None:
    """
    Configure the root logger.

    - File handler: rotating log, max 5 MB × 3 backup files, with timestamps.
    - Console handler: WARNING and above only (the Rich dashboard owns the
      terminal, so we keep console noise minimal).
    """
    root = logging.getLogger()
    root.setLevel(level)

    fmt = logging.Formatter(
        fmt="%(asctime)s [%(levelname)-8s] %(name)s — %(message)s",
        datefmt="%Y-%m-%dT%H:%M:%S",
    )

    # Rotating file handler — keeps the last ~15 MB of logs.
    file_handler = logging.handlers.RotatingFileHandler(
        LOG_PATH, maxBytes=5 * 1024 * 1024, backupCount=3, encoding="utf-8"
    )
    file_handler.setLevel(logging.DEBUG)
    file_handler.setFormatter(fmt)
    root.addHandler(file_handler)

    # Console handler — only warnings and errors bubble up to the terminal.
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.WARNING)
    console_handler.setFormatter(fmt)
    root.addHandler(console_handler)
