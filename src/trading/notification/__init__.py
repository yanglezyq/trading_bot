"""Notification module: push alerts and trade results to external channels."""

from .feishu import FeishuWebhook

__all__ = ["FeishuWebhook"]
