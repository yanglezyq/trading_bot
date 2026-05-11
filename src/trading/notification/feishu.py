"""Feishu (Lark) Webhook integration for trading notifications.

Sends messages to a Feishu group via custom bot webhook.
All sends are fire-and-forget: failures are logged but never block the main flow.
"""

from __future__ import annotations

import logging
import time
from typing import Optional

import httpx

logger = logging.getLogger(__name__)


class FeishuWebhook:
    """Send notifications to a Feishu group bot webhook.

    Supports:
    - send_text(title, content): plain text message
    - send_card(title, elements): interactive card (rich text)

    All methods are synchronous and non-blocking (timeout=5s).
    On any failure, logs warning and returns False.
    """

    def __init__(self, webhook_url: str, timeout: float = 5.0) -> None:
        self.webhook_url = webhook_url
        self.timeout = timeout
        self._last_alert_times: dict[str, float] = {}
        self._dedup_interval = 300.0  # 5 minutes

    @property
    def enabled(self) -> bool:
        """True if webhook URL is configured."""
        return bool(self.webhook_url)

    def _should_send(self, dedup_key: Optional[str] = None) -> bool:
        """Check dedup: same key within 5min is suppressed."""
        if not self.enabled:
            return False
        if dedup_key is None:
            return True
        now = time.time()
        last = self._last_alert_times.get(dedup_key, 0)
        if now - last < self._dedup_interval:
            return False
        self._last_alert_times[dedup_key] = now
        return True

    def _post(self, payload: dict) -> bool:
        """Post JSON to webhook. Returns True on success."""
        try:
            resp = httpx.post(
                self.webhook_url,
                json=payload,
                timeout=self.timeout,
            )
            if resp.status_code == 200:
                body = resp.json()
                if body.get("code", 0) == 0:
                    return True
                logger.warning("Feishu webhook returned error: %s", body)
                return False
            logger.warning("Feishu webhook HTTP %d: %s", resp.status_code, resp.text[:200])
            return False
        except Exception as exc:
            logger.warning("Feishu webhook failed: %s", exc)
            return False

    def send_text(self, title: str, content: str, dedup_key: Optional[str] = None) -> bool:
        """Send a plain text message to Feishu group.

        Args:
            title: Message title (bold header line).
            content: Message body text.
            dedup_key: If provided, same key within 5min won't be resent.

        Returns:
            True if sent successfully, False otherwise.
        """
        if not self._should_send(dedup_key):
            return False

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": "blue",
                },
                "elements": [
                    {
                        "tag": "markdown",
                        "content": content,
                    }
                ],
            },
        }
        return self._post(payload)

    def send_card(
        self,
        title: str,
        elements: list[dict],
        template: str = "blue",
        dedup_key: Optional[str] = None,
    ) -> bool:
        """Send an interactive card message to Feishu group.

        Args:
            title: Card header title.
            elements: List of card element dicts (markdown, divider, etc).
            template: Header color template (blue/green/red/yellow/purple).
            dedup_key: Dedup key for suppression.

        Returns:
            True if sent successfully.
        """
        if not self._should_send(dedup_key):
            return False

        payload = {
            "msg_type": "interactive",
            "card": {
                "header": {
                    "title": {"tag": "plain_text", "content": title},
                    "template": template,
                },
                "elements": elements,
            },
        }
        return self._post(payload)

    # ------------------------------------------------------------------
    # Convenience methods for trading scenarios
    # ------------------------------------------------------------------

    def notify_risk_alert(self, symbol: str, alerts: list[str], direction: str = "", mark_price: float = 0.0, funding_rate: float = 0.0) -> bool:
        """Push RiskDaemon alerts."""
        if not alerts:
            return False
        dedup_key = f"risk:{symbol}:{alerts[0]}"
        lines = [f"**{a}**" for a in alerts]
        extra = []
        if mark_price > 0:
            extra.append(f"当前价: {mark_price:.4f}")
        if direction:
            extra.append(f"方向: {direction}")
        if funding_rate != 0:
            extra.append(f"Funding: {funding_rate:.4%}")
        if extra:
            lines.append(" | ".join(extra))
        content = "\n".join(lines)
        return self.send_text(f"⚠️ [风控告警] {symbol}", content, dedup_key=dedup_key)

    def notify_pipeline_complete(
        self,
        symbol: str,
        stance: str,
        confidence: float,
        action: str,
        size_pct: float,
        leverage: int,
        risk_approved: bool,
        risk_rationale: str,
        entry_price: float = 0.0,
        stop_loss_price: float = 0.0,
        take_profit_price: float = 0.0,
    ) -> bool:
        """Push pipeline completion summary."""
        status_icon = "✅" if risk_approved else "❌"
        risk_label = "approved" if risk_approved else "rejected"
        lines = [
            f"**研究**: {stance} (confidence: {confidence:.0%})",
            f"**计划**: {action} {size_pct:.1f}% @ {leverage}x",
            f"**风控**: {status_icon} {risk_label}",
        ]
        if risk_rationale:
            lines.append(f"**理由**: {risk_rationale[:100]}")
        price_parts = []
        if entry_price > 0:
            price_parts.append(f"入场: ${entry_price:,.2f}")
        if stop_loss_price > 0:
            price_parts.append(f"SL: ${stop_loss_price:,.2f}")
        if take_profit_price > 0:
            price_parts.append(f"TP: ${take_profit_price:,.2f}")
        if price_parts:
            lines.append(" | ".join(price_parts))
        content = "\n".join(lines)
        return self.send_text(f"📊 [交易分析完成] {symbol}", content)

    def notify_execution(self, symbol: str, action: str, quantity: float, message: str) -> bool:
        """Push order execution confirmation."""
        content = f"**{action}** {quantity} {symbol}\n{message}"
        return self.send_text(f"🎯 [下单执行] {symbol}", content)

    def notify_sl_failed(self, symbol: str, action: str, message: str) -> bool:
        """Push CRITICAL: stop-loss failed alert."""
        content = f"**{action}** {symbol}\n{message}\n\n**请立即人工检查仓位并手动设置止损！**"
        return self.send_text(f"🚨 [CRITICAL] SL挂单失败 {symbol}", content, dedup_key=f"sl_fail:{symbol}")

    def notify_batch_scan(self, results: list[dict]) -> bool:
        """Push batch scan summary (only signals, not neutral).

        Each result dict should have: symbol, stance, confidence, action, size_pct, leverage.
        """
        if not results:
            return False
        lines = [f"扫描到 **{len(results)}** 个有信号币种：", ""]
        for r in results:
            stance = r.get("stance", "?")
            icon = "🟢" if stance == "long" else "🔴" if stance == "short" else "⚪"
            lines.append(
                f"{icon} **{r['symbol']}** | {stance} {r.get('confidence', 0):.0%} | "
                f"{r.get('action', '?')} {r.get('size_pct', 0):.1f}% @ {r.get('leverage', 1)}x"
            )
        content = "\n".join(lines)
        return self.send_text(f"📋 [批量扫描结果] {len(results)} 信号", content)

    def notify_rebalance(self, results: list[dict]) -> bool:
        """Push portfolio rebalance summary.

        Each result dict should have: symbol, action, status, message.
        """
        if not results:
            return False
        executed = [r for r in results if r.get("status") == "executed"]
        failed = [r for r in results if r.get("status") == "failed"]
        lines = [f"调仓执行完成: **{len(executed)}** 成功 / **{len(failed)}** 失败", ""]
        for r in results:
            icon = "✅" if r.get("status") == "executed" else "❌" if r.get("status") == "failed" else "⏭️"
            lines.append(f"{icon} **{r['symbol']}** | {r.get('action', '?')} | {r.get('message', '')}")
        content = "\n".join(lines)
        return self.send_text(f"⚖️ [组合调仓] {len(executed)}成功/{len(results)}总计", content)
