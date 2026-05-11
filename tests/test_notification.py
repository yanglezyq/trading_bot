"""Tests for the Feishu notification module."""

from unittest.mock import MagicMock, patch

import pytest

from trading.notification.feishu import FeishuWebhook


class TestFeishuWebhook:
    """Unit tests for FeishuWebhook."""

    def test_enabled_when_url_set(self):
        hook = FeishuWebhook("https://open.feishu.cn/open-apis/bot/v2/hook/test123")
        assert hook.enabled is True

    def test_disabled_when_url_empty(self):
        hook = FeishuWebhook("")
        assert hook.enabled is False

    def test_send_text_returns_false_when_disabled(self):
        hook = FeishuWebhook("")
        assert hook.send_text("title", "content") is False

    @patch("trading.notification.feishu.httpx.post")
    def test_send_text_success(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0, "msg": "success"}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        result = hook.send_text("Test Title", "Hello world")

        assert result is True
        mock_post.assert_called_once()
        call_kwargs = mock_post.call_args
        payload = call_kwargs.kwargs["json"] if "json" in call_kwargs.kwargs else call_kwargs[1]["json"]
        assert payload["msg_type"] == "interactive"
        assert payload["card"]["header"]["title"]["content"] == "Test Title"
        assert payload["card"]["elements"][0]["content"] == "Hello world"

    @patch("trading.notification.feishu.httpx.post")
    def test_send_text_http_error(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 500
        mock_resp.text = "Internal Server Error"
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        result = hook.send_text("title", "content")
        assert result is False

    @patch("trading.notification.feishu.httpx.post")
    def test_send_text_network_error(self, mock_post):
        mock_post.side_effect = Exception("Connection timeout")

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        result = hook.send_text("title", "content")
        assert result is False

    @patch("trading.notification.feishu.httpx.post")
    def test_dedup_suppresses_duplicate(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        # First send: goes through
        assert hook.send_text("t", "c", dedup_key="key1") is True
        # Second send with same key: suppressed
        assert hook.send_text("t", "c", dedup_key="key1") is False
        # Different key: goes through
        assert hook.send_text("t", "c", dedup_key="key2") is True
        assert mock_post.call_count == 2

    @patch("trading.notification.feishu.httpx.post")
    def test_send_card(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        elements = [{"tag": "markdown", "content": "**bold**"}]
        result = hook.send_card("Card Title", elements, template="green")

        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        assert payload["card"]["header"]["template"] == "green"

    @patch("trading.notification.feishu.httpx.post")
    def test_notify_risk_alert(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        result = hook.notify_risk_alert(
            symbol="BTCUSDT",
            alerts=["INVALIDATION HIT 93000 <= 93500"],
            direction="LONG",
            mark_price=92800.0,
            funding_rate=0.00012,
        )
        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        header = payload["card"]["header"]["title"]["content"]
        assert "BTCUSDT" in header
        assert "风控告警" in header

    @patch("trading.notification.feishu.httpx.post")
    def test_notify_pipeline_complete(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        result = hook.notify_pipeline_complete(
            symbol="ETHUSDT",
            stance="long",
            confidence=0.8,
            action="open_long",
            size_pct=3.0,
            leverage=10,
            risk_approved=True,
            risk_rationale="All rules passed",
            entry_price=3500.0,
            stop_loss_price=3300.0,
            take_profit_price=4000.0,
        )
        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        content = payload["card"]["elements"][0]["content"]
        assert "long" in content
        assert "open_long" in content

    @patch("trading.notification.feishu.httpx.post")
    def test_notify_batch_scan(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        results = [
            {"symbol": "BTCUSDT", "stance": "long", "confidence": 0.7, "action": "open_long", "size_pct": 3.0, "leverage": 10},
            {"symbol": "ETHUSDT", "stance": "short", "confidence": 0.65, "action": "open_short", "size_pct": 2.0, "leverage": 5},
        ]
        result = hook.notify_batch_scan(results)
        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        content = payload["card"]["elements"][0]["content"]
        assert "BTCUSDT" in content
        assert "ETHUSDT" in content

    def test_notify_batch_scan_empty(self):
        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        assert hook.notify_batch_scan([]) is False

    @patch("trading.notification.feishu.httpx.post")
    def test_notify_sl_failed(self, mock_post):
        mock_resp = MagicMock()
        mock_resp.status_code = 200
        mock_resp.json.return_value = {"code": 0}
        mock_post.return_value = mock_resp

        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        result = hook.notify_sl_failed("SOLUSDT", "open_long", "Order rejected by exchange")
        assert result is True
        payload = mock_post.call_args.kwargs["json"]
        header = payload["card"]["header"]["title"]["content"]
        assert "CRITICAL" in header

    def test_notify_risk_alert_empty_alerts(self):
        hook = FeishuWebhook("https://open.feishu.cn/hook/test")
        assert hook.notify_risk_alert("BTC", alerts=[]) is False
