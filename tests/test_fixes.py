"""Regression tests for the 5 fragility fixes."""

import json
import tempfile
from unittest.mock import MagicMock, patch

import pytest

from trading.ai.schemas import ExecutionPlan, ExecutionResult, ResearchDecision
from trading.core.config import (
    AppConfig, BinanceConfig, ClaudeConfig, LoggingConfig,
    MonitorConfig, RiskConfig, TradingConfig, VaultConfig,
)
from trading.exchange.orders import OrderManager
from trading.pipeline.persistence import TradeRunDB
from trading.pipeline.runner import TradePipeline, _step_round, is_trading_api_configured


# ────────────────────────────── shared fixtures ──────────────────────────────

@pytest.fixture
def tmp_db(tmp_path):
    return str(tmp_path / "test.db")


@pytest.fixture
def config_with_api(tmp_db):
    return AppConfig(
        vault=VaultConfig(path="/tmp"),
        risk=RiskConfig(),
        binance=BinanceConfig(api_key="testkey", api_secret="testsecret"),
        claude=ClaudeConfig(api_key="sk-ant-test"),
        trading=TradingConfig(max_position_size_pct=0.05, combined_ai_calls=False),
        monitor=MonitorConfig(),
        logging=LoggingConfig(sqlite_db=tmp_db),
    )


def _mock_research() -> ResearchDecision:
    return ResearchDecision(
        symbol="BTCUSDT", stance="long", confidence=0.8,
        thesis="T", market_structure="uptrend", evidence=[],
        catalysts=[], risks=[], invalidation="", time_horizon="swing",
        preferred_market="futures",
    )


def _mock_plan(action="open_long", size_pct=3.0, leverage=10,
               sl=5.0, tp=15.0) -> ExecutionPlan:
    return ExecutionPlan(
        action=action, size_pct=size_pct, leverage=leverage,
        entry_idea="", stop_loss_pct=sl, take_profit_pct=tp, rationale="",
    )


# ──────────────────────────────────────────────────────────────────────────── #
# Fix 1: mark_price() and change_leverage() exist on UMFutures                #
# ──────────────────────────────────────────────────────────────────────────── #

class TestUMFuturesNewMethods:
    def test_mark_price_method_exists(self):
        from trading.exchange.um_futures import UMFutures
        assert callable(getattr(UMFutures, "mark_price", None)), \
            "mark_price() must be defined on UMFutures"

    def test_change_leverage_method_exists(self):
        from trading.exchange.um_futures import UMFutures
        assert callable(getattr(UMFutures, "change_leverage", None)), \
            "change_leverage() must be defined on UMFutures"

    def test_get_position_mode_method_exists(self):
        from trading.exchange.um_futures import UMFutures
        assert callable(getattr(UMFutures, "get_position_mode", None))

    def test_get_symbol_lot_size_method_exists(self):
        from trading.exchange.um_futures import UMFutures
        assert callable(getattr(UMFutures, "get_symbol_lot_size", None))

    def test_mark_price_calls_correct_endpoint(self):
        from trading.exchange.um_futures import UMFutures
        client = UMFutures.__new__(UMFutures)
        client.query = MagicMock(return_value={"markPrice": "50000.0"})
        result = client.mark_price(symbol="BTCUSDT")
        client.query.assert_called_once_with(
            "/fapi/v1/premiumIndex", {"symbol": "BTCUSDT"}
        )
        assert result["markPrice"] == "50000.0"

    def test_change_leverage_calls_correct_endpoint(self):
        from trading.exchange.um_futures import UMFutures
        client = UMFutures.__new__(UMFutures)
        client.sign_request = MagicMock(return_value={"leverage": 10})
        result = client.change_leverage(symbol="BTCUSDT", leverage=10)
        client.sign_request.assert_called_once_with(
            "POST", "/fapi/v1/leverage", {"symbol": "BTCUSDT", "leverage": 10}
        )

    def test_get_symbol_lot_size_parses_exchange_info(self):
        from trading.exchange.um_futures import UMFutures
        fake_info = {
            "symbols": [{
                "symbol": "DOGEUSDT",
                "filters": [
                    {"filterType": "PRICE_FILTER", "tickSize": "0.00001"},
                    {"filterType": "LOT_SIZE", "stepSize": "1"},
                ],
            }]
        }
        client = UMFutures.__new__(UMFutures)
        client._exchange_info_cache = None
        client._exchange_info_ts = 0.0
        client._exchange_info_ttl = 3600.0
        client.query = MagicMock(return_value=fake_info)
        step = client.get_symbol_lot_size("DOGEUSDT")
        assert step == 1.0

    def test_get_symbol_lot_size_fallback_on_missing(self):
        from trading.exchange.um_futures import UMFutures
        client = UMFutures.__new__(UMFutures)
        client._exchange_info_cache = None
        client._exchange_info_ts = 0.0
        client._exchange_info_ttl = 3600.0
        client.query = MagicMock(return_value={"symbols": []})
        step = client.get_symbol_lot_size("UNKNOWN")
        assert step == 0.001

    def test_get_symbol_lot_size_fallback_on_exception(self):
        from trading.exchange.um_futures import UMFutures
        client = UMFutures.__new__(UMFutures)
        client.query = MagicMock(side_effect=RuntimeError("network"))
        step = client.get_symbol_lot_size("BTCUSDT")
        assert step == 0.001

    def test_depth_method_exists(self):
        from trading.exchange.um_futures import UMFutures
        assert callable(getattr(UMFutures, "depth", None)), \
            "depth() must be defined on UMFutures"

    def test_funding_rate_history_method_exists(self):
        from trading.exchange.um_futures import UMFutures
        assert callable(getattr(UMFutures, "funding_rate_history", None)), \
            "funding_rate_history() must be defined on UMFutures"

    def test_depth_calls_correct_endpoint(self):
        from trading.exchange.um_futures import UMFutures
        client = UMFutures.__new__(UMFutures)
        client.query = MagicMock(return_value={"bids": [], "asks": []})
        result = client.depth(symbol="BTCUSDT", limit=20)
        client.query.assert_called_once_with(
            "/fapi/v1/depth", {"symbol": "BTCUSDT", "limit": 20}
        )

    def test_funding_rate_history_calls_correct_endpoint(self):
        from trading.exchange.um_futures import UMFutures
        client = UMFutures.__new__(UMFutures)
        client.query = MagicMock(return_value=[{"fundingRate": "0.0001"}])
        result = client.funding_rate_history(symbol="BTCUSDT", limit=3)
        client.query.assert_called_once_with(
            "/fapi/v1/fundingRate", {"symbol": "BTCUSDT", "limit": 3}
        )


# ──────────────────────────────────────────────────────────────────────────── #
# Fix 2: quantity step-size rounding                                           #
# ──────────────────────────────────────────────────────────────────────────── #

class TestStepRound:
    def test_btc_step_001(self):
        # BTC stepSize=0.001 → keep 3 dp, floor
        assert _step_round(0.0678, 0.001) == 0.067

    def test_doge_step_1(self):
        # DOGE stepSize=1 → integer, floor
        assert _step_round(6.789, 1.0) == 6.0

    def test_sol_step_01(self):
        # SOL stepSize=0.1 → 1 dp, floor
        assert _step_round(1.234, 0.1) == 1.2

    def test_floor_not_round(self):
        # Must floor, not round — 0.0095 with step 0.001 → 0.009 not 0.010
        assert _step_round(0.0095, 0.001) == 0.009

    def test_zero_step_falls_back(self):
        result = _step_round(1.23456789, 0.0)
        assert result == round(1.23456789, 3)

    def test_step_round_used_in_do_execute(self, config_with_api):
        """_do_execute() calls get_symbol_lot_size() and passes result to _step_round()."""
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        captured_quantities = []

        def fake_place_market(symbol, side, quantity, reduce_only=False,
                               position_side=None, dry_run=None):
            captured_quantities.append(quantity)
            return {"orderId": 1}

        with (
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
        ):
            mock_binance = MagicMock()
            # stepSize=1 → DOGE-like integer quantity
            mock_binance.futures_client.get_symbol_lot_size.return_value = 1.0
            mock_binance.futures_client.mark_price.return_value = {"markPrice": "0.15"}
            mock_binance.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            mock_binance.futures_client.change_leverage.return_value = {}
            MockBinance.return_value = mock_binance

            mock_order = MagicMock()
            mock_order.place_market_order.side_effect = fake_place_market
            mock_order.place_stop_market_order.return_value = {"orderId": 2}
            mock_order.place_take_profit_market_order.return_value = {"orderId": 3}
            MockOrder.return_value = mock_order

            pipeline._do_execute(
                symbol="DOGEUSDT",
                market="auto",
                final_action="open_long",
                final_size_pct=3.0,
                final_leverage=10,
                plan=_mock_plan(),
                account_summary={"futures_balance_usdt": 10000.0},
                positions=[],
            )

        assert len(captured_quantities) == 1
        qty = captured_quantities[0]
        # 10000 * 3% * 10 / 0.15 = 20000, with step=1 → 20000 (integer)
        assert qty == float(int(qty)), f"quantity {qty} should be an integer for stepSize=1"


# ──────────────────────────────────────────────────────────────────────────── #
# Fix 3: Hedge Mode → positionSide replaces reduceOnly                        #
# ──────────────────────────────────────────────────────────────────────────── #

class TestHedgeMode:
    def test_apply_position_side_hedge_mode(self):
        params: dict = {}
        OrderManager._apply_position_side(params, reduce_only=True, position_side="LONG")
        assert params == {"positionSide": "LONG"}
        assert "reduceOnly" not in params

    def test_apply_position_side_one_way_close(self):
        params: dict = {}
        OrderManager._apply_position_side(params, reduce_only=True, position_side=None)
        assert params == {"reduceOnly": "true"}
        assert "positionSide" not in params

    def test_apply_position_side_one_way_open(self):
        params: dict = {}
        OrderManager._apply_position_side(params, reduce_only=False, position_side=None)
        assert params == {}

    def test_hedge_mode_open_long_uses_position_side_long(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        captured_calls: list[dict] = []

        def fake_place_market(symbol, side, quantity, reduce_only=False,
                               position_side=None, dry_run=None):
            captured_calls.append({"reduce_only": reduce_only, "position_side": position_side})
            return {"orderId": 1}

        with (
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
        ):
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            # Simulate HEDGE MODE
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": True}
            mock_b.futures_client.change_leverage.return_value = {}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.side_effect = fake_place_market
            mock_o.place_stop_market_order.return_value = {"orderId": 2}
            mock_o.place_take_profit_market_order.return_value = {"orderId": 3}
            MockOrder.return_value = mock_o

            pipeline._do_execute(
                symbol="BTCUSDT",
                market="auto",
                final_action="open_long",
                final_size_pct=3.0,
                final_leverage=10,
                plan=_mock_plan(),
                account_summary={"futures_balance_usdt": 10000.0},
                positions=[],
            )

        assert len(captured_calls) == 1
        call = captured_calls[0]
        assert call["position_side"] == "LONG"
        assert call["reduce_only"] is False   # must be False in hedge mode

    def test_hedge_mode_close_long_uses_position_side_long(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        captured_calls: list[dict] = []

        def fake_place_market(symbol, side, quantity, reduce_only=False,
                               position_side=None, dry_run=None):
            captured_calls.append({"reduce_only": reduce_only, "position_side": position_side})
            return {"orderId": 1}

        with (
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
        ):
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": True}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.side_effect = fake_place_market
            MockOrder.return_value = mock_o

            positions = [{"symbol": "BTCUSDT", "direction": "LONG", "amount": 0.06}]
            pipeline._do_execute(
                symbol="BTCUSDT",
                market="auto",
                final_action="close_long",
                final_size_pct=100.0,
                final_leverage=10,
                plan=_mock_plan(action="close_long"),
                account_summary={"futures_balance_usdt": 10000.0},
                positions=positions,
            )

        assert captured_calls[0]["position_side"] == "LONG"
        assert captured_calls[0]["reduce_only"] is False

    def test_one_way_mode_close_uses_reduce_only(self, config_with_api):
        pipeline = TradePipeline(config=config_with_api, dry_run=False)
        captured_calls: list[dict] = []

        def fake_place_market(symbol, side, quantity, reduce_only=False,
                               position_side=None, dry_run=None):
            captured_calls.append({"reduce_only": reduce_only, "position_side": position_side})
            return {"orderId": 1}

        with (
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
        ):
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            # ONE-WAY MODE
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.side_effect = fake_place_market
            MockOrder.return_value = mock_o

            positions = [{"symbol": "BTCUSDT", "direction": "LONG", "amount": 0.06}]
            pipeline._do_execute(
                symbol="BTCUSDT",
                market="auto",
                final_action="close_long",
                final_size_pct=100.0,
                final_leverage=10,
                plan=_mock_plan(action="close_long"),
                account_summary={"futures_balance_usdt": 10000.0},
                positions=positions,
            )

        assert captured_calls[0]["reduce_only"] is True
        assert captured_calls[0]["position_side"] is None


# ──────────────────────────────────────────────────────────────────────────── #
# Fix 4: duplicate execution protection                                        #
# ──────────────────────────────────────────────────────────────────────────── #

class TestDuplicateProtection:
    def test_get_recent_executed_run_returns_none_when_empty(self, tmp_db):
        db = TradeRunDB(tmp_db)
        assert db.get_recent_executed_run("BTCUSDT") is None

    def test_get_recent_executed_run_finds_recent_real_execution(self, tmp_db):
        db = TradeRunDB(tmp_db)
        result = ExecutionResult(
            executed=True, status="executed", symbol="BTCUSDT",
            final_action="open_long", final_size_pct=3.0, message="ok",
        )
        db.save_run(
            symbol="BTCUSDT", model="test",
            research_decision=None, execution_plan=None, risk_decision=None,
            execution_result=result,
            account_snapshot=None, position_snapshot=None,
            dry_run=False,
        )
        found = db.get_recent_executed_run("BTCUSDT", within_seconds=300)
        assert found is not None

    def test_get_recent_executed_run_ignores_dry_run_row(self, tmp_db):
        db = TradeRunDB(tmp_db)
        result = ExecutionResult(
            executed=False, status="dry_run", symbol="BTCUSDT",
            final_action="open_long", final_size_pct=3.0, message="dryrun",
        )
        db.save_run(
            symbol="BTCUSDT", model="test",
            research_decision=None, execution_plan=None, risk_decision=None,
            execution_result=result,
            account_snapshot=None, position_snapshot=None,
            dry_run=True,      # dry_run=1 → excluded
        )
        found = db.get_recent_executed_run("BTCUSDT", within_seconds=300)
        assert found is None

    def test_pipeline_blocks_second_run_within_window(self, config_with_api):
        """After a real execution, second call within 5 min returns blocked_duplicate."""
        from trading.pipeline.runner import TradePipeline
        pipeline = TradePipeline(config=config_with_api, dry_run=False)

        # Manually inject a recent executed run into the DB
        real_result = ExecutionResult(
            executed=True, status="executed", symbol="BTCUSDT",
            final_action="open_long", final_size_pct=3.0, message="ok",
        )
        pipeline.db.save_run(
            symbol="BTCUSDT", model="test",
            research_decision=None, execution_plan=None, risk_decision=None,
            execution_result=real_result,
            account_snapshot=None, position_snapshot=None,
            dry_run=False,
        )

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False)

        assert result.status == "blocked_duplicate"
        assert result.executed is False
        assert "5 分钟" in result.message

    def test_pipeline_dry_run_not_blocked(self, config_with_api):
        """Dry-run mode bypasses the dedup check (it never places orders)."""
        pipeline = TradePipeline(config=config_with_api, dry_run=True)

        # Inject a recent executed run
        real_result = ExecutionResult(
            executed=True, status="executed", symbol="BTCUSDT",
            final_action="open_long", final_size_pct=3.0, message="ok",
        )
        pipeline.db.save_run(
            symbol="BTCUSDT", model="test",
            research_decision=None, execution_plan=None, risk_decision=None,
            execution_result=real_result,
            account_snapshot=None, position_snapshot=None,
            dry_run=False,
        )

        with (
            patch.object(pipeline.advisor, "generate_research", return_value=_mock_research()),
            patch.object(pipeline.advisor, "generate_execution_plan", return_value=_mock_plan()),
            patch.object(pipeline, "_get_vault_context", return_value=""),
        ):
            _, _, _, result = pipeline.run("BTCUSDT", write_vault=False)

        # dry_run → status=dry_run, never reaches dedup check
        assert result.status == "dry_run"

    def test_success_field_only_set_for_real_executions(self, tmp_db):
        """success=1 only when executed=True, not for dry_run or hold."""
        db = TradeRunDB(tmp_db)

        for status, executed, dry_run_flag in [
            ("executed", True, False),
            ("dry_run", False, True),
            ("not_executed_missing_api", False, False),
            ("blocked_by_risk", False, False),
        ]:
            r = ExecutionResult(
                executed=executed, status=status, symbol="BTCUSDT",
                final_action="open_long", final_size_pct=3.0, message="",
            )
            db.save_run(
                symbol="BTCUSDT", model="test",
                research_decision=None, execution_plan=None, risk_decision=None,
                execution_result=r,
                account_snapshot=None, position_snapshot=None,
                dry_run=dry_run_flag,
            )

        import sqlite3
        with sqlite3.connect(tmp_db) as conn:
            rows = conn.execute(
                "SELECT success, dry_run, execution_result_json FROM trade_runs ORDER BY id"
            ).fetchall()

        statuses_and_success = [
            (json.loads(r[2])["status"], r[0], r[1]) for r in rows
        ]
        # Only "executed" row should have success=1
        assert statuses_and_success[0] == ("executed", 1, 0)
        assert statuses_and_success[1] == ("dry_run", 0, 1)
        assert statuses_and_success[2] == ("not_executed_missing_api", 0, 0)
        assert statuses_and_success[3] == ("blocked_by_risk", 0, 0)


# ──────────────────────────────────────────────────────────────────────────── #
# Fix 5: SL/TP status explicitly tracked in ExecutionResult                   #
# ──────────────────────────────────────────────────────────────────────────── #

class TestSlTpTracking:
    def _run_do_execute(self, config, sl_resp, tp_resp, plan=None):
        pipeline = TradePipeline(config=config, dry_run=False)
        if plan is None:
            plan = _mock_plan()

        with (
            patch("trading.pipeline.runner.BinanceClient") as MockBinance,
            patch("trading.pipeline.runner.OrderManager") as MockOrder,
        ):
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            mock_b.futures_client.change_leverage.return_value = {}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.return_value = {"orderId": 100}
            mock_o.place_stop_market_order.return_value = sl_resp
            mock_o.place_take_profit_market_order.return_value = tp_resp
            MockOrder.return_value = mock_o

            return pipeline._do_execute(
                symbol="BTCUSDT",
                market="auto",
                final_action="open_long",
                final_size_pct=3.0,
                final_leverage=10,
                plan=plan,
                account_summary={"futures_balance_usdt": 10000.0},
                positions=[],
            )

    def test_both_sl_tp_placed(self, config_with_api):
        result = self._run_do_execute(
            config_with_api,
            sl_resp={"orderId": 200},
            tp_resp={"orderId": 300},
        )
        assert result.sl_status == "placed"
        assert result.tp_status == "placed"
        assert result.sl_order_id == "200"
        assert result.tp_order_id == "300"
        assert result.executed is True
        assert result.status == "executed"
        assert len(result.order_ids) == 3   # main + sl + tp

    def test_sl_placed_tp_failed(self, config_with_api):
        with patch("trading.pipeline.runner.BinanceClient") as MockBinance, \
             patch("trading.pipeline.runner.OrderManager") as MockOrder:
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            mock_b.futures_client.change_leverage.return_value = {}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.return_value = {"orderId": 100}
            mock_o.place_stop_market_order.return_value = {"orderId": 200}
            mock_o.place_take_profit_market_order.side_effect = RuntimeError("TP rejected")
            MockOrder.return_value = mock_o

            pipeline = TradePipeline(config=config_with_api, dry_run=False)
            result = pipeline._do_execute(
                symbol="BTCUSDT", market="auto",
                final_action="open_long", final_size_pct=3.0, final_leverage=10,
                plan=_mock_plan(), account_summary={"futures_balance_usdt": 10000.0},
                positions=[],
            )

        # Main order succeeded, SL succeeded, TP failed
        assert result.executed is True
        assert result.status == "executed"
        assert result.sl_status == "placed"
        assert result.tp_status == "failed"
        assert result.tp_order_id is None
        assert len(result.order_ids) == 2   # main + sl only
        assert "TP:FAILED" in result.message

    def test_both_sl_tp_failed(self, config_with_api):
        with patch("trading.pipeline.runner.BinanceClient") as MockBinance, \
             patch("trading.pipeline.runner.OrderManager") as MockOrder:
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            mock_b.futures_client.change_leverage.return_value = {}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.return_value = {"orderId": 100}
            mock_o.place_stop_market_order.side_effect = RuntimeError("SL rejected")
            mock_o.place_take_profit_market_order.side_effect = RuntimeError("TP rejected")
            MockOrder.return_value = mock_o

            pipeline = TradePipeline(config=config_with_api, dry_run=False)
            result = pipeline._do_execute(
                symbol="BTCUSDT", market="auto",
                final_action="open_long", final_size_pct=3.0, final_leverage=10,
                plan=_mock_plan(), account_summary={"futures_balance_usdt": 10000.0},
                positions=[],
            )

        assert result.executed is True       # main order went through
        assert result.status == "executed"   # still executed
        assert result.sl_status == "failed"
        assert result.tp_status == "failed"
        assert len(result.order_ids) == 1    # only main order id
        assert "SL:FAILED" in result.message
        assert "TP:FAILED" in result.message

    def test_sl_tp_skipped_when_close_action(self, config_with_api):
        """Close actions must NOT attach SL/TP orders."""
        with patch("trading.pipeline.runner.BinanceClient") as MockBinance, \
             patch("trading.pipeline.runner.OrderManager") as MockOrder:
            mock_b = MagicMock()
            mock_b.futures_client.get_symbol_lot_size.return_value = 0.001
            mock_b.futures_client.mark_price.return_value = {"markPrice": "50000"}
            mock_b.futures_client.get_position_mode.return_value = {"dualSidePosition": False}
            MockBinance.return_value = mock_b

            mock_o = MagicMock()
            mock_o.place_market_order.return_value = {"orderId": 100}
            MockOrder.return_value = mock_o

            pipeline = TradePipeline(config=config_with_api, dry_run=False)
            positions = [{"symbol": "BTCUSDT", "direction": "LONG", "amount": 0.06}]
            result = pipeline._do_execute(
                symbol="BTCUSDT", market="auto",
                final_action="close_long", final_size_pct=100.0, final_leverage=10,
                plan=_mock_plan(action="close_long"), account_summary={"futures_balance_usdt": 10000.0},
                positions=positions,
            )

        assert result.sl_status == "skipped"
        assert result.tp_status == "skipped"
        mock_o.place_stop_market_order.assert_not_called()
        mock_o.place_take_profit_market_order.assert_not_called()

    def test_result_to_dict_includes_sl_tp_fields(self):
        """Serialised ExecutionResult must include the new SL/TP fields."""
        r = ExecutionResult(
            executed=True, status="executed", symbol="BTCUSDT",
            final_action="open_long", final_size_pct=3.0, message="ok",
            sl_status="placed", tp_status="failed",
            sl_order_id="200", tp_order_id=None,
        )
        d = r.to_dict()
        assert d["sl_status"] == "placed"
        assert d["tp_status"] == "failed"
        assert d["sl_order_id"] == "200"
        assert d["tp_order_id"] is None
