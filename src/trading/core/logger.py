"""Structured logging: Rich console + SQLite dual output."""

import json
import logging
import sqlite3
from datetime import datetime
from pathlib import Path
from typing import Any, Optional

from rich.console import Console
from rich.logging import RichHandler
from rich.table import Table

from .config import LoggingConfig


class SQLiteHandler(logging.Handler):
    """Logging handler that persists records to SQLite."""

    def __init__(self, db_path: str):
        """Initialize and create the logs table if it does not exist."""
        super().__init__()
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        self._init_db()

    def _init_db(self) -> None:
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(
                """
                CREATE TABLE IF NOT EXISTS logs (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    timestamp TEXT NOT NULL,
                    level TEXT NOT NULL,
                    logger_name TEXT,
                    message TEXT,
                    extra_data TEXT,
                    created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP
                )
                """
            )
            conn.execute("CREATE INDEX IF NOT EXISTS idx_timestamp ON logs(timestamp)")
            conn.execute("CREATE INDEX IF NOT EXISTS idx_level ON logs(level)")
            conn.commit()

    def emit(self, record: logging.LogRecord) -> None:
        """Write a log record to SQLite."""
        try:
            extra_data = getattr(record, "extra", {})
            with sqlite3.connect(self.db_path) as conn:
                conn.execute(
                    "INSERT INTO logs (timestamp, level, logger_name, message, extra_data) VALUES (?, ?, ?, ?, ?)",
                    (
                        datetime.fromtimestamp(record.created).isoformat(),
                        record.levelname,
                        record.name,
                        self.format(record),
                        json.dumps(extra_data) if extra_data else None,
                    ),
                )
                conn.commit()
        except Exception:
            self.handleError(record)


class TradingLogger:
    """Dual-output logger: Rich console + SQLite."""

    def __init__(self, name: str, config: LoggingConfig):
        """Initialize Rich console and SQLite handlers from config."""
        self.config = config
        self.logger = logging.getLogger(name)
        self.logger.setLevel(getattr(logging, config.level))
        self.logger.handlers.clear()

        rich_handler = RichHandler(console=Console())
        rich_handler.setFormatter(logging.Formatter(fmt="%(message)s", datefmt="[%X]"))
        self.logger.addHandler(rich_handler)

        sqlite_handler = SQLiteHandler(config.sqlite_db)
        sqlite_handler.setFormatter(
            logging.Formatter(fmt="%(asctime)s - %(name)s - %(levelname)s - %(message)s")
        )
        self.logger.addHandler(sqlite_handler)

    def _log(self, level: int, message: str, extra: Optional[dict[str, Any]]) -> None:
        if not self.logger.isEnabledFor(level):
            return
        if extra:
            record = self.logger.makeRecord(self.logger.name, level, "", 0, message, (), None)
            record.extra = extra  # type: ignore[attr-defined]
            self.logger.handle(record)
        else:
            self.logger.log(level, message)

    def info(self, message: str, extra: Optional[dict[str, Any]] = None) -> None:
        """Log at INFO level."""
        self._log(logging.INFO, message, extra)

    def warning(self, message: str, extra: Optional[dict[str, Any]] = None) -> None:
        """Log at WARNING level."""
        self._log(logging.WARNING, message, extra)

    def error(self, message: str, extra: Optional[dict[str, Any]] = None) -> None:
        """Log at ERROR level."""
        self._log(logging.ERROR, message, extra)

    def debug(self, message: str, extra: Optional[dict[str, Any]] = None) -> None:
        """Log at DEBUG level."""
        self._log(logging.DEBUG, message, extra)

    @staticmethod
    def get_logs_table(db_path: str, limit: int = 50, level: Optional[str] = None) -> Table:
        """Return recent log records from SQLite as a Rich Table."""
        table = Table(title="Trading Logs")
        table.add_column("时间", style="cyan")
        table.add_column("级别", style="magenta")
        table.add_column("消息")

        try:
            with sqlite3.connect(db_path) as conn:
                cursor = conn.cursor()
                if level:
                    cursor.execute(
                        "SELECT timestamp, level, message FROM logs WHERE level = ? ORDER BY id DESC LIMIT ?",
                        (level, limit),
                    )
                else:
                    cursor.execute(
                        "SELECT timestamp, level, message FROM logs ORDER BY id DESC LIMIT ?",
                        (limit,),
                    )

                for timestamp, log_level, message in cursor.fetchall():
                    style = "red" if log_level == "ERROR" else "yellow" if log_level == "WARNING" else "white"
                    table.add_row(timestamp, f"[{style}]{log_level}[/{style}]", message)
        except Exception as e:
            table.add_row("ERROR", "ERROR", str(e))

        return table
