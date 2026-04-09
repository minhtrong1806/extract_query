"""Standard logger for the application.

Provides a clean, configurable wrapper around Python's logging module.
Features:
- Console and optional rotating file handler
- ISO8601 timestamps with timezone
- Includes source filename and line number in each record so you can identify which file emitted the log
- Convenience `get_logger` factory

Usage:
    from src.config.logger import get_logger
    logger = get_logger(__name__)
    logger.info("Hello world")
"""

from __future__ import annotations

import logging
from logging.handlers import RotatingFileHandler
from datetime import datetime, timezone
from pathlib import Path
from typing import Optional


class Iso8601Formatter(logging.Formatter):
    """Formatter that outputs ISO8601 timestamps with timezone info."""

    def formatTime(
        self, record: logging.LogRecord, datefmt: Optional[str] = None
    ) -> str:
        dt = datetime.fromtimestamp(record.created, tz=timezone.utc).astimezone()
        return dt.isoformat(timespec="milliseconds")


class StandardLogger:
    """A small wrapper to create and configure loggers consistently.

    Parameters
    - name: Logger name (usually __name__)
    - level: Default logging level (logging.INFO)
    - log_to_file: If True, add a rotating file handler
    - log_dir: Path to directory where logs will be written if log_to_file is True
    - filename: Base filename for the rotating log file
    - max_bytes, backup_count: RotatingFileHandler settings
    - include_full_path: If True, include the full pathname in logs, otherwise just the base filename
    - log_airflow: If True, also add Airflow's logger handler (when running in Airflow)

    Methods: debug/info/warning/error/critical/exception
    """

    DEFAULT_FORMAT = (
        "%(asctime)s %(levelname)-8s [%(name)s:%(filename)s:%(lineno)d] %(message)s"
    )
    DEFAULT_FULLPATH_FORMAT = (
        "%(asctime)s %(levelname)-8s [%(name)s:%(pathname)s:%(lineno)d] %(message)s"
    )

    def __init__(
        self,
        name: str,
        level: int = logging.INFO,
        log_to_file: bool = False,
        log_dir: Optional[str] = None,
        filename: str = "app.log",
        max_bytes: int = 10 * 1024 * 1024,
        backup_count: int = 5,
        include_full_path: bool = False,
        log_airflow: bool = False,
        force: bool = False,
        file_mode: str = "w",
    ) -> None:
        self.name = name
        self.logger = logging.getLogger(name)
        if getattr(self.logger, "_standard_logger_configured", False) and not force:
            self._set_level(level)
            return

        self.logger.propagate = False
        self._set_level(level)

        fmt = self.DEFAULT_FULLPATH_FORMAT if include_full_path else self.DEFAULT_FORMAT
        formatter = Iso8601Formatter(fmt)

        console = logging.StreamHandler()
        console.setFormatter(formatter)
        console.setLevel(level)
        self.logger.addHandler(console)

        if log_to_file:
            if log_dir is None:
                raise ValueError("log_dir must be provided when log_to_file=True")
            # Wrap trong try/except để tránh crash lúc import khi chạy trong container
            # có permission issue (vd: sau git pull chown đổi ownership mounted volume).
            # Fallback về console-only logging thay vì raise exception phá vỡ DAG parsing.
            try:
                log_path = Path(log_dir)
                log_path.mkdir(parents=True, exist_ok=True)
                file_handler = RotatingFileHandler(
                    filename=str(log_path / filename),
                    mode=file_mode,
                    maxBytes=max_bytes,
                    backupCount=backup_count,
                    encoding="utf-8",
                )
                file_handler.setFormatter(formatter)
                file_handler.setLevel(level)
                self.logger.addHandler(file_handler)
            except (PermissionError, OSError) as exc:
                # Không raise — chỉ log warning để DAG vẫn parse được
                self.logger.warning(
                    "[StandardLogger] Cannot create file handler for log_dir='%s': %s. "
                    "Falling back to console-only logging.",
                    log_dir,
                    exc,
                )

        if log_airflow:
            self._add_airflow_handler(level, formatter)

        setattr(self.logger, "_standard_logger_configured", True)

    def _add_airflow_handler(self, level: int, formatter: logging.Formatter) -> None:
        """Add Airflow's logger handler if available."""
        try:
            from airflow.utils.log.logging_mixin import LoggingMixin

            # Get Airflow's task logger
            airflow_logger = LoggingMixin().log

            # Only add handler if this logger doesn't already have Airflow handler
            if not any(
                isinstance(h, type(airflow_logger)) for h in self.logger.handlers
            ):
                # Airflow logger is already configured, just propagate to it
                self.logger.propagate = True
        except ImportError:
            # Airflow not available, silently skip
            pass
        except Exception as e:
            # Log warning but don't fail
            self.logger.warning(f"Could not add Airflow handler: {e}")

    def _set_level(self, level: int) -> None:
        self.logger.setLevel(level)

    def debug(self, msg: str, *args, **kwargs) -> None:
        self.logger.debug(msg, *args, **kwargs)

    def info(self, msg: str, *args, **kwargs) -> None:
        self.logger.info(msg, *args, **kwargs)

    def warning(self, msg: str, *args, **kwargs) -> None:
        self.logger.warning(msg, *args, **kwargs)

    warn = warning

    def error(self, msg: str, *args, **kwargs) -> None:
        self.logger.error(msg, *args, **kwargs)

    def critical(self, msg: str, *args, **kwargs) -> None:
        self.logger.critical(msg, *args, **kwargs)

    def exception(self, msg: str, *args, exc_info: bool = True, **kwargs) -> None:
        self.logger.error(msg, *args, exc_info=exc_info, **kwargs)


def get_logger(
    name: str,
    level: int = logging.INFO,
    log_to_file: bool = False,
    log_dir: Optional[str] = None,
    filename: str = "app.log",
    max_bytes: int = 10 * 1024 * 1024,
    backup_count: int = 5,
    include_full_path: bool = False,
    log_airflow: bool = False,
    force: bool = False,
    file_mode: str = "a",
) -> logging.Logger:
    """Factory to get a configured logger instance.

    Example:
        logger = get_logger(__name__, log_to_file=True, log_dir="./logs", log_airflow=True)
    """
    _ = StandardLogger(
        name=name,
        level=level,
        log_to_file=log_to_file,
        log_dir=log_dir,
        filename=filename,
        max_bytes=max_bytes,
        backup_count=backup_count,
        include_full_path=include_full_path,
        log_airflow=log_airflow,
        force=force,
        file_mode=file_mode,
    )
    return logging.getLogger(name)


__all__ = ["StandardLogger", "get_logger"]


"""
Example usage:

from src.config.logger import get_logger

logger = get_logger(__name__, log_to_file=True, log_dir="./logs")
logger.info("Ứng dụng khởi động")

from src.config.logger import get_logger
logger = get_logger(__name__, log_to_file=True, log_dir="./logs")

"""
