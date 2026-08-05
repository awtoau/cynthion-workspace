#!/usr/bin/env python3
"""
Logging utilities with color coding and file output for Cynthion workspace.

Provides structured logging for installation, build, and diagnostic operations
with simultaneous console (colored) and file output.

Usage:
    from logging_utils import setup_logging

    logger = setup_logging("install.py", log_dir=Path("./tmp/logs"))
    logger.info("Installation started")
    logger.error("Build failed", extra={"component": "apollo"})
"""

import logging
import sys
import os
from pathlib import Path
from typing import Optional
from datetime import datetime


class ColoredFormatter(logging.Formatter):
    """Logging formatter with ANSI color codes for terminal output."""

    # ANSI color codes
    COLORS = {
        logging.DEBUG: "\033[36m",      # Cyan
        logging.INFO: "\033[96m",       # Bright cyan
        logging.WARNING: "\033[93m",    # Bright yellow
        logging.ERROR: "\033[91m",      # Bright red
        logging.CRITICAL: "\033[91m",   # Bright red
    }

    RESET = "\033[0m"
    BOLD = "\033[1m"

    def __init__(self, fmt: Optional[str] = None, use_color: bool = True):
        """
        Initialize colored formatter.

        Args:
            fmt: Format string for log messages
            use_color: Whether to use ANSI color codes
        """
        super().__init__(fmt)
        self.use_color = use_color
        if fmt is None:
            self._fmt = "[%(levelname)s] %(message)s"

    def format(self, record: logging.LogRecord) -> str:
        """Format log record with optional colors."""
        if self.use_color:
            color = self.COLORS.get(record.levelno, self.RESET)
            record.levelname = f"{self.BOLD}{color}[{record.levelname}]{self.RESET}"
        else:
            record.levelname = f"[{record.levelname}]"

        return super().format(record)


def setup_logging(
    name: str,
    log_dir: Optional[Path] = None,
    level: int = logging.INFO,
    console_only: bool = False,
    filename: Optional[str] = None,
    file_only: bool = False,
) -> logging.Logger:
    """
    Setup logger with console and optional file output.

    Args:
        name: Logger name (typically script name or component name)
        log_dir: Directory for log files. If None, console only
        level: Logging level (default: INFO)
        console_only: Disable file logging even if log_dir provided
        filename: Fixed log filename, appended to across runs. The default
            instead opens a new <name>-<timestamp>.log per run, which suits a
            long build worth keeping separately but leaves a file behind every
            time -- wrong for something run as often as a pre-push hook
        file_only: Skip the console handler, for a caller that prints its own
            report and would otherwise emit each line twice

    Returns:
        Configured logger instance

    Example:
        logger = setup_logging("build", log_dir=Path("./tmp/logs"))
        logger.info("Build started")
        logger.error("Compilation failed")
    """
    logger = logging.getLogger(name)
    logger.setLevel(level)

    # Remove existing handlers to avoid duplicates
    logger.handlers.clear()

    # Console handler with colors
    if not file_only:
        console_handler = logging.StreamHandler(sys.stdout)
        console_handler.setLevel(level)
        console_formatter = ColoredFormatter(
            fmt="%(levelname)s %(message)s",
            use_color=True
        )
        console_handler.setFormatter(console_formatter)
        logger.addHandler(console_handler)

    # File handler if log_dir provided
    if log_dir and not console_only:
        log_dir.mkdir(parents=True, exist_ok=True)
        if filename:
            log_file = log_dir / filename
        else:
            timestamp = datetime.now().strftime("%Y%m%d-%H%M%S")
            log_file = log_dir / f"{name}-{timestamp}.log"

        file_handler = logging.FileHandler(log_file)
        file_handler.setLevel(logging.DEBUG)
        file_formatter = logging.Formatter(
            fmt="%(asctime)s [%(levelname)s] %(message)s",
            datefmt="%Y-%m-%d %H:%M:%S"
        )
        file_handler.setFormatter(file_formatter)
        logger.addHandler(file_handler)

        # Log that file logging is active
        logger.debug(f"File logging: {log_file}")

    return logger
