"""SQLite persistence for complete trade pipeline runs and trade reflections."""

from collections import Counter
from datetime import datetime, timedelta
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
            adaptive_context_json TEXT,
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

    _CREATE_EQUITY_SNAPSHOTS = """
        CREATE TABLE IF NOT EXISTS equity_snapshots (
            id         INTEGER PRIMARY KEY AUTOINCREMENT,
            created_at TIMESTAMP DEFAULT CURRENT_TIMESTAMP,
            balance    REAL NOT NULL
        )
    """

    def __init__(self, db_path: str):
        self.db_path = Path(db_path)
        self.db_path.parent.mkdir(parents=True, exist_ok=True)
        with sqlite3.connect(self.db_path) as conn:
            conn.execute(self._CREATE_RUNS)
            conn.execute(self._CREATE_REFLECTIONS)
            conn.execute(self._CREATE_EQUITY_SNAPSHOTS)
            cols = {row[1] for row in conn.execute("PRAGMA table_info(trade_runs)").fetchall()}
            if "market_snapshot_json" not in cols:
                conn.execute("ALTER TABLE trade_runs ADD COLUMN market_snapshot_json TEXT")
            if "onchain_snapshot_json" not in cols:
                conn.execute("ALTER TABLE trade_runs ADD COLUMN onchain_snapshot_json TEXT")
            if "adaptive_context_json" not in cols:
                conn.execute("ALTER TABLE trade_runs ADD COLUMN adaptive_context_json TEXT")
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
        adaptive_context: Optional[dict] = None,
        onchain_snapshot: Optional[dict] = None,
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
                    execution_result_json, account_snapshot_json, market_snapshot_json,
                    adaptive_context_json,
                    onchain_snapshot_json, position_snapshot_json,
                    dry_run, report_path, success)
                   VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)""",
                (
                    symbol.upper(),
                    model,
                    _dump(research_decision),
                    _dump(execution_plan),
                    _dump(risk_decision),
                    _dump(execution_result),
                    _dump(account_snapshot),
                    _dump(market_snapshot),
                    _dump(adaptive_context),
                    _dump(onchain_snapshot),
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

    @staticmethod
    def _safe_json_loads(payload: Optional[str]) -> dict:
        """Parse JSON payload into a dict, returning {} on failure."""
        if not payload:
            return {}
        try:
            value = json.loads(payload)
        except Exception:
            return {}
        return value if isinstance(value, dict) else {}

    def find_run_link_candidate(
        self,
        symbol: str,
        reference_time: Optional[datetime] = None,
        lookback_hours: int = 72,
    ) -> Optional[dict]:
        """Find the most recent run for symbol before reference_time within lookback window."""
        rows = self.get_runs(symbol=symbol, limit=50)
        if not rows:
            return None

        ref_ts = reference_time or datetime.now()
        earliest = ref_ts - timedelta(hours=lookback_hours)
        candidates: list[dict] = []

        for row in rows:
            raw_ts = row.get("created_at")
            if not raw_ts:
                continue
            try:
                created_at = datetime.fromisoformat(str(raw_ts))
            except ValueError:
                continue
            if earliest <= created_at <= ref_ts:
                row = dict(row)
                row["created_at_dt"] = created_at
                candidates.append(row)

        if not candidates:
            return None
        candidates.sort(key=lambda r: r["created_at_dt"], reverse=True)
        best = dict(candidates[0])
        best.pop("created_at_dt", None)
        return best

    def _get_linked_trade_outcomes(self, run_ids: list[int]) -> dict[int, list[dict]]:
        """Return linked closed-trade outcomes grouped by run_id."""
        if not run_ids:
            return {}
        placeholders = ", ".join("?" for _ in run_ids)
        sql = (
            "SELECT linked_run_id, symbol, direction, realized_pnl, pnl_pct "
            f"FROM trade_records WHERE linked_run_id IN ({placeholders})"
        )
        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, run_ids).fetchall()
        except sqlite3.OperationalError:
            return {}

        grouped: dict[int, list[dict]] = {}
        for row in rows:
            d = dict(row)
            run_id = int(d.pop("linked_run_id"))
            grouped.setdefault(run_id, []).append(d)
        return grouped

    @staticmethod
    def _aggregate_replay_groups(samples: list[dict], field: str) -> list[dict]:
        """Aggregate replay samples by a categorical field."""
        groups: dict[str, dict] = {}
        for sample in samples:
            key = "overall" if field == "__overall__" else str(sample.get(field) or "unknown")
            agg = groups.setdefault(
                key,
                {
                    "group": key,
                    "samples": 0,
                    "wins": 0,
                    "total_realized_pnl": 0.0,
                    "total_pnl_pct": 0.0,
                    "total_confidence": 0.0,
                },
            )
            pnl = float(sample.get("realized_pnl", 0.0) or 0.0)
            agg["samples"] += 1
            agg["total_realized_pnl"] += pnl
            agg["total_pnl_pct"] += float(sample.get("pnl_pct", 0.0) or 0.0)
            agg["total_confidence"] += float(sample.get("confidence", 0.0) or 0.0)
            if pnl > 0:
                agg["wins"] += 1

        rows: list[dict] = []
        for key, agg in groups.items():
            samples_n = agg["samples"]
            rows.append(
                {
                    "group": key,
                    "samples": samples_n,
                    "win_rate": agg["wins"] / samples_n * 100 if samples_n else None,
                    "total_realized_pnl": agg["total_realized_pnl"],
                    "avg_realized_pnl": agg["total_realized_pnl"] / samples_n if samples_n else 0.0,
                    "avg_pnl_pct": agg["total_pnl_pct"] / samples_n if samples_n else 0.0,
                    "avg_confidence": agg["total_confidence"] / samples_n if samples_n else 0.0,
                }
            )
        rows.sort(key=lambda row: (-row["samples"], row["group"]))
        return rows

    @staticmethod
    def _aggregate_edge_groups(samples: list[dict], field: str, min_samples: int = 2) -> list[dict]:
        """Aggregate replay samples into trading-edge metrics by a categorical field."""
        groups: dict[str, dict] = {}
        for sample in samples:
            key = str(sample.get(field) or "unknown")
            agg = groups.setdefault(
                key,
                {
                    "group": key,
                    "samples": 0,
                    "wins": 0,
                    "losses": 0,
                    "total_realized_pnl": 0.0,
                    "total_pnl_pct": 0.0,
                    "sum_win_pnl": 0.0,
                    "sum_loss_abs_pnl": 0.0,
                    "sum_win_pnl_pct": 0.0,
                    "sum_loss_abs_pnl_pct": 0.0,
                },
            )
            pnl = float(sample.get("realized_pnl", 0.0) or 0.0)
            pnl_pct = float(sample.get("pnl_pct", 0.0) or 0.0)
            agg["samples"] += 1
            agg["total_realized_pnl"] += pnl
            agg["total_pnl_pct"] += pnl_pct
            if pnl > 0:
                agg["wins"] += 1
                agg["sum_win_pnl"] += pnl
                agg["sum_win_pnl_pct"] += pnl_pct
            elif pnl < 0:
                agg["losses"] += 1
                agg["sum_loss_abs_pnl"] += abs(pnl)
                agg["sum_loss_abs_pnl_pct"] += abs(pnl_pct)

        rows: list[dict] = []
        for agg in groups.values():
            samples_n = agg["samples"]
            if samples_n < min_samples:
                continue
            wins = agg["wins"]
            losses = agg["losses"]
            win_rate = wins / samples_n * 100 if samples_n else None
            loss_rate = losses / samples_n * 100 if samples_n else None
            avg_win_pnl = agg["sum_win_pnl"] / wins if wins else 0.0
            avg_loss_pnl = agg["sum_loss_abs_pnl"] / losses if losses else 0.0
            avg_win_pnl_pct = agg["sum_win_pnl_pct"] / wins if wins else 0.0
            avg_loss_pnl_pct = agg["sum_loss_abs_pnl_pct"] / losses if losses else 0.0
            win_rate_ratio = wins / samples_n if samples_n else 0.0
            loss_rate_ratio = losses / samples_n if samples_n else 0.0
            expectancy_pnl = win_rate_ratio * avg_win_pnl - loss_rate_ratio * avg_loss_pnl
            expectancy_pnl_pct = win_rate_ratio * avg_win_pnl_pct - loss_rate_ratio * avg_loss_pnl_pct
            profit_factor = (
                agg["sum_win_pnl"] / agg["sum_loss_abs_pnl"]
                if agg["sum_loss_abs_pnl"] > 0
                else None
            )
            payoff_ratio = avg_win_pnl / avg_loss_pnl if avg_loss_pnl > 0 else None
            rows.append(
                {
                    "group": agg["group"],
                    "samples": samples_n,
                    "wins": wins,
                    "losses": losses,
                    "win_rate": win_rate,
                    "loss_rate": loss_rate,
                    "avg_win_pnl": avg_win_pnl,
                    "avg_loss_pnl": avg_loss_pnl,
                    "avg_win_pnl_pct": avg_win_pnl_pct,
                    "avg_loss_pnl_pct": avg_loss_pnl_pct,
                    "payoff_ratio": payoff_ratio,
                    "expectancy_pnl": expectancy_pnl,
                    "expectancy_pnl_pct": expectancy_pnl_pct,
                    "profit_factor": profit_factor,
                    "total_realized_pnl": agg["total_realized_pnl"],
                    "avg_realized_pnl": agg["total_realized_pnl"] / samples_n if samples_n else 0.0,
                }
            )
        rows.sort(
            key=lambda row: (
                -(row["expectancy_pnl"] or 0.0),
                -(row["profit_factor"] or 0.0),
                -row["samples"],
                row["group"],
            )
        )
        return rows

    def get_replay_samples(self, symbol: Optional[str] = None, limit: int = 200) -> list[dict]:
        """Return linked trade outcomes enriched with run-time context for replay analysis."""
        sql = """
            SELECT
                tr.id AS trade_id,
                tr.symbol AS trade_symbol,
                tr.direction,
                tr.realized_pnl,
                tr.pnl_pct,
                tr.open_time,
                tr.close_time,
                tr.notes,
                tr.linked_run_id,
                runs.created_at AS run_created_at,
                runs.research_decision_json,
                runs.execution_plan_json,
                runs.risk_decision_json,
                runs.execution_result_json,
                runs.market_snapshot_json,
                runs.adaptive_context_json
            FROM trade_records tr
            INNER JOIN trade_runs runs ON runs.id = tr.linked_run_id
            WHERE tr.linked_run_id IS NOT NULL
        """
        params: list = []
        if symbol:
            sql += " AND tr.symbol = ?"
            params.append(symbol.upper())
        sql += " ORDER BY tr.close_time DESC LIMIT ?"
        params.append(limit)

        try:
            with sqlite3.connect(self.db_path) as conn:
                conn.row_factory = sqlite3.Row
                rows = conn.execute(sql, params).fetchall()
        except sqlite3.OperationalError:
            return []

        samples: list[dict] = []
        for row in rows:
            data = dict(row)
            research = self._safe_json_loads(data.pop("research_decision_json", None))
            plan = self._safe_json_loads(data.pop("execution_plan_json", None))
            risk = self._safe_json_loads(data.pop("risk_decision_json", None))
            result = self._safe_json_loads(data.pop("execution_result_json", None))
            market = self._safe_json_loads(data.pop("market_snapshot_json", None))
            adaptive = self._safe_json_loads(data.pop("adaptive_context_json", None))

            warnings = risk.get("warnings") or []
            violations = risk.get("violated_rules") or []
            samples.append(
                {
                    "trade_id": data.get("trade_id"),
                    "run_id": data.get("linked_run_id"),
                    "symbol": data.get("trade_symbol", ""),
                    "direction": data.get("direction", ""),
                    "realized_pnl": float(data.get("realized_pnl", 0.0) or 0.0),
                    "pnl_pct": float(data.get("pnl_pct", 0.0) or 0.0),
                    "open_time": data.get("open_time"),
                    "close_time": data.get("close_time"),
                    "notes": data.get("notes", ""),
                    "run_created_at": data.get("run_created_at"),
                    "stance": str(research.get("stance", "unknown")),
                    "confidence": float(research.get("confidence", 0.0) or 0.0),
                    "consensus_strength": float(research.get("consensus_strength", 1.0) or 1.0),
                    "action": str(plan.get("action", "unknown")),
                    "final_action": str(result.get("final_action", plan.get("action", "unknown"))),
                    "risk_approved": bool(risk.get("approved", False)),
                    "setup_quality_score": float(risk.get("setup_quality_score", 0.0) or 0.0),
                    "setup_quality_grade": str(risk.get("setup_quality_grade", "")),
                    "gating_profile": str(risk.get("gating_profile", "")),
                    "edge_policy_label": str(risk.get("edge_policy_label", "")),
                    "edge_policy_expectancy_pnl_pct": float(risk.get("edge_policy_expectancy_pnl_pct", 0.0) or 0.0),
                    "opportunity_score": float(risk.get("opportunity_score", 0.0) or 0.0),
                    "opportunity_bucket": str(risk.get("opportunity_bucket", "")),
                    "btc_market_regime": str(market.get("btc_market_regime", "unknown")),
                    "execution_template": str(market.get("execution_template", "unknown")),
                    "narrative_tag": str(market.get("narrative_tag", "unknown")),
                    "asset_tier": str(market.get("asset_tier", "unknown")),
                    "profile_mode": str(adaptive.get("profile_mode", "none")),
                    "preferred_bias": str(adaptive.get("preferred_bias", "")),
                    "warnings": [str(w) for w in warnings],
                    "violated_rules": [str(v) for v in violations],
                }
            )
        return samples

    def get_replay_stats(self, symbol: Optional[str] = None, limit: int = 200) -> dict:
        """Aggregate linked outcomes into replay-style analytics."""
        samples = self.get_replay_samples(symbol=symbol, limit=limit)
        total = len(samples)
        if total == 0:
            return {
                "samples_scanned": 0,
                "unique_symbols": 0,
                "win_rate": None,
                "total_realized_pnl": 0.0,
                "avg_realized_pnl": None,
                "avg_pnl_pct": None,
                "avg_confidence": None,
                "by_regime": [],
                "by_template": [],
                "by_narrative": [],
                "by_action": [],
                "by_profile_mode": [],
                "by_quality_grade": [],
                "by_edge_policy": [],
                "by_opportunity_bucket": [],
                "loss_warning_patterns": [],
            }

        wins = sum(1 for sample in samples if float(sample["realized_pnl"]) > 0)
        total_pnl = sum(float(sample["realized_pnl"]) for sample in samples)
        total_pnl_pct = sum(float(sample["pnl_pct"]) for sample in samples)
        total_confidence = sum(float(sample["confidence"]) for sample in samples)
        unique_symbols = len({str(sample["symbol"]) for sample in samples})

        loss_warning_counter: Counter[str] = Counter()
        for sample in samples:
            if float(sample["realized_pnl"]) < 0:
                loss_warning_counter.update(sample.get("warnings") or [])

        return {
            "samples_scanned": total,
            "unique_symbols": unique_symbols,
            "win_rate": wins / total * 100 if total else None,
            "total_realized_pnl": total_pnl,
            "avg_realized_pnl": total_pnl / total if total else None,
            "avg_pnl_pct": total_pnl_pct / total if total else None,
            "avg_confidence": total_confidence / total if total else None,
            "by_regime": self._aggregate_replay_groups(samples, "btc_market_regime"),
            "by_template": self._aggregate_replay_groups(samples, "execution_template"),
            "by_narrative": self._aggregate_replay_groups(samples, "narrative_tag"),
            "by_action": self._aggregate_replay_groups(samples, "final_action"),
            "by_profile_mode": self._aggregate_replay_groups(samples, "profile_mode"),
            "by_quality_grade": self._aggregate_replay_groups(samples, "setup_quality_grade"),
            "by_edge_policy": self._aggregate_replay_groups(samples, "edge_policy_label"),
            "by_opportunity_bucket": self._aggregate_replay_groups(samples, "opportunity_bucket"),
            "loss_warning_patterns": loss_warning_counter.most_common(10),
        }

    def get_edge_stats(
        self,
        symbol: Optional[str] = None,
        limit: int = 200,
        min_samples: int = 2,
    ) -> dict:
        """Return expectancy-style edge stats from linked replay samples."""
        samples = self.get_replay_samples(symbol=symbol, limit=limit)
        total = len(samples)
        if total == 0:
            return {
                "samples_scanned": 0,
                "unique_symbols": 0,
                "overall": {},
                "by_regime": [],
                "by_template": [],
                "by_narrative": [],
                "by_quality_grade": [],
                "by_profile_mode": [],
                "by_edge_policy": [],
                "by_opportunity_bucket": [],
                "top_positive_edges": [],
            }

        overall_rows = self._aggregate_edge_groups(samples, "__overall__", min_samples=1)
        overall = overall_rows[0] if overall_rows else {}
        by_regime = self._aggregate_edge_groups(samples, "btc_market_regime", min_samples=min_samples)
        by_template = self._aggregate_edge_groups(samples, "execution_template", min_samples=min_samples)
        by_narrative = self._aggregate_edge_groups(samples, "narrative_tag", min_samples=min_samples)
        by_quality_grade = self._aggregate_edge_groups(samples, "setup_quality_grade", min_samples=min_samples)
        by_profile_mode = self._aggregate_edge_groups(samples, "gating_profile", min_samples=min_samples)
        by_edge_policy = self._aggregate_edge_groups(samples, "edge_policy_label", min_samples=min_samples)
        by_opportunity_bucket = self._aggregate_edge_groups(samples, "opportunity_bucket", min_samples=min_samples)

        top_positive_edges = sorted(
            by_template + by_regime + by_narrative + by_edge_policy + by_opportunity_bucket,
            key=lambda row: (
                -(row["expectancy_pnl"] or 0.0),
                -(row["profit_factor"] or 0.0),
                -row["samples"],
            ),
        )[:10]

        return {
            "samples_scanned": total,
            "unique_symbols": len({str(sample["symbol"]) for sample in samples}),
            "overall": overall,
            "by_regime": by_regime,
            "by_template": by_template,
            "by_narrative": by_narrative,
            "by_quality_grade": by_quality_grade,
            "by_profile_mode": by_profile_mode,
            "by_edge_policy": by_edge_policy,
            "by_opportunity_bucket": by_opportunity_bucket,
            "top_positive_edges": top_positive_edges,
        }

    @staticmethod
    def _finalize_rule_effectiveness(rule_stats: dict[str, dict]) -> list[dict]:
        """Turn raw per-rule aggregates into sorted analytics rows."""
        rows: list[dict] = []
        for rule, agg in rule_stats.items():
            linked_trades = agg["linked_trades"]
            avg_pnl = agg["total_realized_pnl"] / linked_trades if linked_trades else 0.0
            avg_pnl_pct = agg["total_pnl_pct"] / linked_trades if linked_trades else 0.0
            win_rate = agg["wins"] / linked_trades * 100 if linked_trades else None
            rows.append(
                {
                    "rule": rule,
                    "count": agg["count"],
                    "linked_trades": linked_trades,
                    "win_rate": win_rate,
                    "total_realized_pnl": agg["total_realized_pnl"],
                    "avg_realized_pnl": avg_pnl,
                    "avg_pnl_pct": avg_pnl_pct,
                }
            )
        rows.sort(key=lambda r: (-r["count"], r["rule"]))
        return rows

    def get_risk_rule_stats(self, symbol: Optional[str] = None, limit: int = 200) -> dict:
        """Aggregate warning/block frequencies and linked outcome stats from stored risk decisions."""
        runs = self.get_runs(symbol=symbol, limit=limit)
        outcomes_by_run = self._get_linked_trade_outcomes(
            [int(row["id"]) for row in runs if row.get("id") is not None]
        )
        warning_counter: Counter[str] = Counter()
        violation_counter: Counter[str] = Counter()
        warning_effectiveness: dict[str, dict] = {}
        violation_effectiveness: dict[str, dict] = {}
        approved_runs = 0
        blocked_runs = 0
        warning_runs = 0
        linked_runs = 0
        linked_trades = 0
        linked_wins = 0
        linked_total_realized_pnl = 0.0
        linked_total_pnl_pct = 0.0

        for row in runs:
            payload = row.get("risk_decision_json")
            if not payload:
                continue
            try:
                risk = json.loads(payload)
            except Exception:
                continue
            warnings = risk.get("warnings") or []
            violations = risk.get("violated_rules") or []
            if risk.get("approved", False):
                approved_runs += 1
            else:
                blocked_runs += 1
            if warnings:
                warning_runs += 1
            warning_counter.update(str(w) for w in warnings)
            violation_counter.update(str(v) for v in violations)

            run_outcomes = outcomes_by_run.get(int(row["id"]), [])
            if run_outcomes:
                linked_runs += 1
            for outcome in run_outcomes:
                pnl = float(outcome.get("realized_pnl", 0.0) or 0.0)
                pnl_pct = float(outcome.get("pnl_pct", 0.0) or 0.0)
                linked_trades += 1
                linked_total_realized_pnl += pnl
                linked_total_pnl_pct += pnl_pct
                if pnl > 0:
                    linked_wins += 1

            for rule in warnings:
                agg = warning_effectiveness.setdefault(
                    str(rule),
                    {
                        "count": 0,
                        "linked_trades": 0,
                        "wins": 0,
                        "total_realized_pnl": 0.0,
                        "total_pnl_pct": 0.0,
                    },
                )
                agg["count"] += 1
                for outcome in run_outcomes:
                    pnl = float(outcome.get("realized_pnl", 0.0) or 0.0)
                    agg["linked_trades"] += 1
                    agg["total_realized_pnl"] += pnl
                    agg["total_pnl_pct"] += float(outcome.get("pnl_pct", 0.0) or 0.0)
                    if pnl > 0:
                        agg["wins"] += 1

            for rule in violations:
                agg = violation_effectiveness.setdefault(
                    str(rule),
                    {
                        "count": 0,
                        "linked_trades": 0,
                        "wins": 0,
                        "total_realized_pnl": 0.0,
                        "total_pnl_pct": 0.0,
                    },
                )
                agg["count"] += 1
                for outcome in run_outcomes:
                    pnl = float(outcome.get("realized_pnl", 0.0) or 0.0)
                    agg["linked_trades"] += 1
                    agg["total_realized_pnl"] += pnl
                    agg["total_pnl_pct"] += float(outcome.get("pnl_pct", 0.0) or 0.0)
                    if pnl > 0:
                        agg["wins"] += 1

        return {
            "runs_scanned": len(runs),
            "approved_runs": approved_runs,
            "blocked_runs": blocked_runs,
            "warning_runs": warning_runs,
            "linked_runs": linked_runs,
            "linked_trades": linked_trades,
            "linked_win_rate": (linked_wins / linked_trades * 100) if linked_trades else None,
            "linked_total_realized_pnl": linked_total_realized_pnl,
            "linked_avg_realized_pnl": (
                linked_total_realized_pnl / linked_trades if linked_trades else None
            ),
            "linked_avg_pnl_pct": (
                linked_total_pnl_pct / linked_trades if linked_trades else None
            ),
            "top_warnings": warning_counter.most_common(10),
            "top_violations": violation_counter.most_common(10),
            "warning_effectiveness": self._finalize_rule_effectiveness(warning_effectiveness),
            "violation_effectiveness": self._finalize_rule_effectiveness(violation_effectiveness),
        }

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

    def count_recent_executed_runs(
        self,
        within_seconds: int,
        symbol: Optional[str] = None,
    ) -> int:
        """Count recent real executions, optionally filtered by symbol."""
        sql = """
            SELECT COUNT(*) FROM trade_runs
            WHERE success = 1
              AND dry_run = 0
              AND created_at >= datetime('now', ?)
        """
        params: list = [f"-{within_seconds} seconds"]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol.upper())

        with sqlite3.connect(self.db_path) as conn:
            row = conn.execute(sql, params).fetchone()
        return int(row[0] if row else 0)

    def sum_recent_executed_notional_usdt(
        self,
        within_seconds: int,
        symbol: Optional[str] = None,
    ) -> float:
        """Sum recent executed notional from stored execution_result_json manual ticket details."""
        sql = """
            SELECT execution_result_json FROM trade_runs
            WHERE success = 1
              AND dry_run = 0
              AND created_at >= datetime('now', ?)
        """
        params: list = [f"-{within_seconds} seconds"]
        if symbol:
            sql += " AND symbol = ?"
            params.append(symbol.upper())

        total = 0.0
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(sql, params).fetchall()
        for row in rows:
            payload = row[0]
            data = self._safe_json_loads(payload)
            details = data.get("manual_order_details") or {}
            total += float(details.get("notional_usdt", 0.0) or 0.0)
        return total

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

    # ------------------------------------------------------------------
    # Equity curve snapshots
    # ------------------------------------------------------------------

    def save_equity_snapshot(self, balance: float) -> int:
        """Record a balance data point for equity curve tracking."""
        with sqlite3.connect(self.db_path) as conn:
            cur = conn.execute(
                "INSERT INTO equity_snapshots (balance) VALUES (?)", (balance,)
            )
            conn.commit()
            return cur.lastrowid  # type: ignore[return-value]

    def get_equity_history(self, limit: int = 30) -> list[float]:
        """Return recent balance history (oldest first) for EMA calculation."""
        with sqlite3.connect(self.db_path) as conn:
            rows = conn.execute(
                "SELECT balance FROM equity_snapshots ORDER BY id DESC LIMIT ?",
                (limit,),
            ).fetchall()
        # Reverse so oldest is first
        return [r[0] for r in reversed(rows)]
