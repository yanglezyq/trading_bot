"""Obsidian Vault reader and writer for trading knowledge base."""

from datetime import datetime
from pathlib import Path
from typing import Optional

from .config import VaultConfig


class VaultReader:
    """Read and write Obsidian Vault files for trading knowledge."""

    def __init__(self, vault_config: VaultConfig):
        """Set up Vault paths from config; raise ValueError if trading directory is missing."""
        self.vault_path = Path(vault_config.path)
        self.trading_dir = vault_config.trading_dir
        self.trading_path = self.vault_path / self.trading_dir

        self.position_tracking_dir = self.trading_path / vault_config.position_tracking_dir
        self.reports_dir = self.trading_path / vault_config.reports_dir

        self.system_guide_file = self.trading_path / vault_config.system_guide
        self.decision_spec_file = self.trading_path / vault_config.decision_spec
        self.research_spec_file = self.trading_path / vault_config.research_spec

        if not self.trading_path.exists():
            raise ValueError(f"Trading directory not found: {self.trading_path}")

    def read_trading_system(self) -> str:
        """Read 合约交易体系-完整指南.md; raise FileNotFoundError if missing."""
        if not self.system_guide_file.exists():
            raise FileNotFoundError(f"System guide not found: {self.system_guide_file}")
        return self.system_guide_file.read_text(encoding="utf-8")

    def read_position_tracking(self) -> Optional[str]:
        """Return the latest 仓位追踪-*.md content from 【持仓管理】, or None."""
        if not self.position_tracking_dir.exists():
            return None

        tracking_files = sorted(
            self.position_tracking_dir.glob("仓位追踪-*.md"),
            reverse=True,
        )
        return tracking_files[0].read_text(encoding="utf-8") if tracking_files else None

    def read_coin_report(self, symbol: str) -> Optional[str]:
        """Return the latest research report for the given symbol, or None."""
        if not self.reports_dir.exists():
            return None

        matching = list(self.reports_dir.glob(f"*{symbol}*.md"))
        if not matching:
            return None

        return max(matching, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8")

    def read_position_decision(self, symbol: str) -> Optional[str]:
        """Return the latest position decision file for the given symbol, or None."""
        if not self.reports_dir.exists():
            return None

        matching = list(self.reports_dir.glob(f"*{symbol}*持仓决策*.md"))
        if not matching:
            return None

        return max(matching, key=lambda p: p.stat().st_mtime).read_text(encoding="utf-8")

    def build_knowledge_context(self, symbols: list[str]) -> str:
        """Assemble system guide, specs, and per-symbol reports into one context string."""
        parts = []

        parts.append("## 交易体系核心规则\n\n" + self.read_trading_system())

        if self.decision_spec_file.exists():
            parts.append("\n\n## 决策建议生成规范\n\n" + self.decision_spec_file.read_text(encoding="utf-8"))

        if self.research_spec_file.exists():
            parts.append("\n\n## 币种调研报告规范\n\n" + self.research_spec_file.read_text(encoding="utf-8"))

        for symbol in symbols:
            if report := self.read_coin_report(symbol):
                parts.append(f"\n\n## {symbol} 研究报告\n\n{report}")
            if decision := self.read_position_decision(symbol):
                parts.append(f"\n\n## {symbol} 持仓决策\n\n{decision}")

        if positions := self.read_position_tracking():
            parts.append("\n\n## 当前仓位\n\n" + positions)

        return "\n".join(parts)

    def write_report(self, filename: str, content: str) -> Path:
        """Write content to 【报告】/; auto-adds prefix and .md extension if missing."""
        self.reports_dir.mkdir(parents=True, exist_ok=True)

        if not filename.startswith("【报告】"):
            filename = f"【报告】{filename}"
        if not filename.endswith(".md"):
            filename += ".md"

        file_path = self.reports_dir / filename
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def update_position_tracking(self, content: str, symbol: Optional[str] = None) -> Path:
        """Write a timestamped position tracking file to 【持仓管理】/."""
        self.position_tracking_dir.mkdir(parents=True, exist_ok=True)

        date_str = datetime.now().strftime("%Y-%m-%d-%H%M%S")
        filename = f"仓位追踪-{symbol}-{date_str}.md" if symbol else f"仓位追踪-{date_str}.md"

        file_path = self.position_tracking_dir / filename
        file_path.write_text(content, encoding="utf-8")
        return file_path

    def list_reports(self, pattern: Optional[str] = None) -> list[Path]:
        """Return sorted list of .md reports, optionally filtered by glob pattern."""
        if not self.reports_dir.exists():
            return []
        return sorted(self.reports_dir.glob(pattern or "*.md"))

    def get_vault_stats(self) -> dict:
        """Return existence and count stats for key Vault directories and files."""
        return {
            "trading_dir_exists": self.trading_path.exists(),
            "reports_count": len(self.list_reports()),
            "position_tracking_count": len(
                list(self.position_tracking_dir.glob("*.md"))
                if self.position_tracking_dir.exists()
                else []
            ),
            "system_guide_exists": self.system_guide_file.exists(),
        }
