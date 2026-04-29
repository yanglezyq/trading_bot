"""Tests for TradeJournal: record, query, and summary logic."""

import tempfile
from datetime import datetime, timedelta

import pytest

from trading.core.journal import TradeJournal, TradeRecord, _calc_pnl


@pytest.fixture
def journal(tmp_path) -> TradeJournal:
    return TradeJournal(db_path=str(tmp_path / "test_trades.db"))


@pytest.fixture
def sample_long(journal: TradeJournal) -> TradeRecord:
    return journal.record_trade(
        symbol="CHZUSDT",
        direction="long",
        entry_price=0.050,
        exit_price=0.068,
        quantity=128000,
        leverage=30,
        open_time=datetime(2025, 1, 1, 10, 0),
        close_time=datetime(2025, 1, 4, 10, 0),
        notes="事件驱动，持仓3天",
    )


@pytest.fixture
def sample_short(journal: TradeJournal) -> TradeRecord:
    return journal.record_trade(
        symbol="ASTERUSDT",
        direction="SHORT",
        entry_price=0.060,
        exit_price=0.075,
        quantity=700,
        leverage=30,
        open_time=datetime(2025, 1, 5, 9, 0),
        close_time=datetime(2025, 1, 5, 15, 0),
        notes="止损出场",
    )


class TestPnlCalc:
    def test_long_profit(self):
        pnl, pct = _calc_pnl("LONG", 0.050, 0.068, 128000, 30)
        assert pnl == pytest.approx(2304.0)
        assert pct == pytest.approx(2304.0 / (0.050 * 128000 / 30) * 100)

    def test_short_profit(self):
        pnl, pct = _calc_pnl("SHORT", 0.060, 0.050, 700, 30)
        assert pnl == pytest.approx(7.0)
        assert pct > 0

    def test_short_loss(self):
        pnl, pct = _calc_pnl("SHORT", 0.060, 0.075, 700, 30)
        assert pnl == pytest.approx(-10.5)
        assert pct < 0

    def test_long_loss(self):
        pnl, pct = _calc_pnl("LONG", 0.050, 0.040, 1000, 10)
        assert pnl == pytest.approx(-10.0)
        assert pct < 0


class TestRecordTrade:
    def test_record_returns_trade_record(self, sample_long: TradeRecord):
        assert sample_long.id is not None
        assert sample_long.symbol == "CHZUSDT"
        assert sample_long.direction == "LONG"
        assert sample_long.is_win is True

    def test_direction_normalized_to_upper(self, journal: TradeJournal):
        t = journal.record_trade("BTCUSDT", "long", 50000, 55000, 1, 10,
                                 open_time=datetime(2025, 1, 1))
        assert t.direction == "LONG"

    def test_symbol_normalized_to_upper(self, journal: TradeJournal):
        t = journal.record_trade("chzusdt", "LONG", 0.05, 0.06, 1000, 10,
                                 open_time=datetime(2025, 1, 1))
        assert t.symbol == "CHZUSDT"

    def test_pnl_auto_calculated(self, sample_long: TradeRecord):
        expected_pnl = (0.068 - 0.050) * 128000
        assert sample_long.realized_pnl == pytest.approx(expected_pnl)

    def test_manual_pnl_overrides_calc(self, journal: TradeJournal):
        t = journal.record_trade("BTCUSDT", "LONG", 50000, 55000, 1, 10,
                                 open_time=datetime(2025, 1, 1),
                                 realized_pnl=999.0)
        assert t.realized_pnl == pytest.approx(999.0)

    def test_close_time_defaults_to_now(self, journal: TradeJournal):
        before = datetime.now()
        t = journal.record_trade("BTCUSDT", "LONG", 1, 2, 1, 1,
                                 open_time=datetime(2025, 1, 1))
        assert t.close_time >= before

    def test_duration_minutes(self, sample_long: TradeRecord):
        assert sample_long.duration_minutes == pytest.approx(3 * 24 * 60)


class TestGetTrades:
    def test_returns_all_trades(self, journal: TradeJournal, sample_long, sample_short):
        trades = journal.get_trades()
        assert len(trades) == 2

    def test_filter_by_symbol(self, journal: TradeJournal, sample_long, sample_short):
        trades = journal.get_trades(symbol="CHZUSDT")
        assert len(trades) == 1
        assert trades[0].symbol == "CHZUSDT"

    def test_filter_symbol_case_insensitive(self, journal: TradeJournal, sample_long):
        trades = journal.get_trades(symbol="chzusdt")
        assert len(trades) == 1

    def test_ordered_by_close_time_desc(self, journal: TradeJournal, sample_long, sample_short):
        trades = journal.get_trades()
        assert trades[0].close_time >= trades[1].close_time

    def test_limit(self, journal: TradeJournal):
        for i in range(5):
            journal.record_trade("BTCUSDT", "LONG", 1.0, 1.1, 1, 10,
                                 open_time=datetime(2025, 1, i + 1))
        assert len(journal.get_trades(limit=3)) == 3

    def test_empty_db(self, journal: TradeJournal):
        assert journal.get_trades() == []


class TestGetSummary:
    def test_empty_returns_total_zero(self, journal: TradeJournal):
        s = journal.get_summary()
        assert s["total_trades"] == 0

    def test_win_rate(self, journal: TradeJournal, sample_long, sample_short):
        s = journal.get_summary()
        # sample_long is win, sample_short is loss
        assert s["wins"] == 1
        assert s["losses"] == 1
        assert s["win_rate"] == pytest.approx(50.0)

    def test_total_pnl(self, journal: TradeJournal, sample_long, sample_short):
        s = journal.get_summary()
        expected = sample_long.realized_pnl + sample_short.realized_pnl
        assert s["total_pnl"] == pytest.approx(expected)

    def test_best_and_worst(self, journal: TradeJournal, sample_long, sample_short):
        s = journal.get_summary()
        assert s["best_trade"].symbol == "CHZUSDT"
        assert s["worst_trade"].symbol == "ASTERUSDT"

    def test_summary_filtered_by_symbol(self, journal: TradeJournal, sample_long, sample_short):
        s = journal.get_summary(symbol="CHZUSDT")
        assert s["total_trades"] == 1
        assert s["wins"] == 1


class TestRichTable:
    def test_table_has_rows(self, journal: TradeJournal, sample_long, sample_short):
        table = journal.get_rich_table()
        assert table.row_count == 2

    def test_table_filtered_by_symbol(self, journal: TradeJournal, sample_long, sample_short):
        table = journal.get_rich_table(symbol="CHZUSDT")
        assert table.row_count == 1

    def test_empty_table(self, journal: TradeJournal):
        table = journal.get_rich_table()
        assert table.row_count == 0
