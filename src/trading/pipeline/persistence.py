"""SQLite persistence for complete trade pipeline runs and trade reflections."""

import json
import sqlite3
from pathlib import Path
from typing import Optional

from ..ai.schemas import ExecutionPlan, ExecutionResult, ResearchDecision, RiskDecision


class TradeRunDB:
    """Persist full trade pipeline runs and AI-generated reflections."""

    _CREATE_RUNS = """
        CREATE TABLE IF NOT EXISTS trade_runs (
            id                    INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at            TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            symbol                TEXT NOT NULL,
            provider              TEXT NOT NULL DEFAULT 'anthropic',
            model                 TEXT NOT NULL,
            research_decision_json TEXT,
            execution_plan_json   TEXT,
            risk_decision_json    TEXT,
            execution_result_json TEXT,
            account_snapshot_json TEXT,
            market_snapshot_json  TEXT,
            position_snapshot_json TEXT,
            dry_run               INTEGER NOT NULL DEFAULT 0,
            report_path           TEXT,
            success               INTEGER NOT NULL DEFAULT 0
        )
    """

    _CREATE_REFLECTIONS = """
        CREATE TABLE IF NOT EXISTS trade_reflections (
            id               INTEGER PRIMARY KEY AUTOINCREMENT,
            symbol           TEXT NOT NULL,
            created_at       TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            trades_analyzed  INTEGER NOT NULL DEFAULT 0,
            direction_accuracy TEXT,
            thesis_evaluation  TEXT,
            lessons          TEXT,
            reflection_text  TEXT,
            model            TEXT,
            source_trade_ids TEXT
        )
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self._CREATE_RUNS)
            conn.execute(self._CREATE_REFLECTIONS)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_runs)").fetchall()}
            if "market_snapshot_json" not in cols:
                conn.execute("ALTER TABLE trade_runs ADD COLUMN market_snapshot_json TEXT")
            conn.commit()

    # ------------------------------------------------------------------
    # Trade runs
    # ------------------------------------------------------------------

    def save_run(
        self,
        symbol: str,
        model: str,
        research_decision: Optional[ResearchDecision],
        execution_plan: Optional[ExecutionPlan],
        risk_decision: Optional[RiskDecision],
        execution_result: Optional[ExecutionResult],
        account_snapshot: Optional[dict],
        position_snapshot: Optional[list],
        dry_run: bool,
        market_snapshot: Optional[dict] = None,
        report_path: Optional[str] = None,
    ) -> int:
        """Insert a complete trade run record. Returns the new row ID."""

        def _dump(obj) -> Optional[str]:
            if obj is None:
                return None
            if hasattr(obj, "to_dict"):
                return json.dumps(obj.to_dict(), ensure_ascii=False)
            return json.dumps(obj, ensure_ascii=False, default=str)

        # fix 4: success=True only when a real order was physically placed.
        # Previously status-based check also caught "dry_run" status,
        # which caused get_recent_executed_run() to fire on hold/dry-run runs.
        success = bool(execution_result is not None and execution_result.executed)

        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO trade_runs
                   (symbol, model,
                    research_decision_json, execution_plan_json, risk_decision_json,
                    execution_result_json, account_snapshot_json, market_snapshot_json, position_snapshot_json,
                    dry_run, report_path, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(),
                    model,
                    _dump(research_decision),
                    _dump(execution_plan),
                    _dump(risk_decision),
                    _dump(execution_result),
                    _dump(account_snapshot),
                    _dump(market_snapshot),
                    _dump(position_snapshot),
                    int(dry_run),
                    report_path,
                    int(success),
                ),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_runs(self, symbol: Optional[str] = None, limit: int = 10) -> list[dict]:
        """Return recent trade run records as plain dicts."""
        sql = "SELECT * FROM trade_runs"
        params: list = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        return [dict(r) for r in rows]

    # ------------------------------------------------------------------
    # Reflections
    # ------------------------------------------------------------------

    def save_reflection(
        self,
        symbol: str,
        model: str,
        trades_analyzed: int,
        reflection_data: dict,
        source_trade_ids: list[int],
    ) -> int:
        """Insert a reflection record. Returns the new row ID."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                """INSERT INTO trade_reflections
                   (symbol, trades_analyzed, direction_accuracy, thesis_evaluation,
                    lessons, reflection_text, model, source_trade_ids)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(),
                    trades_analyzed,
                    reflection_data.get("direction_accuracy", ""),
                    reflection_data.get("thesis_evaluation", ""),
                    json.dumps(reflection_data.get("lessons", []), ensure_ascii=False),
                    reflection_data.get("reflection_text", ""),
                    model,
                    json.dumps(source_trade_ids),
                ),
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_recent_executed_run(
        self, symbol: str, within_seconds: int = 300
    ) -> Optional[dict]:
        """Return the most recent real-execution run for symbol within N seconds, or None.

        Only matches rows where success=1 AND dry_run=0, meaning a live order was placed.
        Uses SQLite datetime() arithmetic — assumes UTC wall clock.
        """
        sql = """
            SELECT * FROM trade_runs
            WHERE  symbol  = ?
              AND  success  = 1
              AND  dry_run  = 0
              AND  created_at >= datetime('now', ?)
            ORDER BY created_at DESC
            LIMIT 1
        """
        delta = f"-{within_seconds} seconds"
        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, (symbol.upper(), delta)).fetchall()
        return dict(rows[0]) if rows else None

    def get_reflections(self, symbol: Optional[str] = None, limit: int = 5) -> list[dict]:
        """Return recent reflections, optionally filtered by symbol."""
        sql = "SELECT * FROM trade_reflections"
        params: list = []
        if symbol:
            sql += " WHERE symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY created_at DESC LIMIT ?"
        params.append(limit)

        with sqlite3.connect(self.db_path) as conn:
            conn.row_factory = sqlite3.Row
            rows = conn.execute(sql, params).fetchall()

        result = []
        for row in rows:
            d = dict(row)
            if d.get("lessons"):
                try:
                    d["lessons"] = json.loads(d["lessons"])
                except (json.JSONDecodeError, TypeError):
                    d["lessons"] = []
            result.append(d)
        return result
