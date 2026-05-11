"""Safe config patch application and rollback helpers."""

from __future__ import annotations

from datetime import datetime
from pathlib import Path
import shutil
from typing import Any, Optional

import yaml


def _deep_merge(base: dict[str, Any], patch: dict[str, Any]) -> dict[str, Any]:
    """Recursively merge patch into base and return the merged dict."""
    merged = dict(base)
    for key, value in patch.items():
        if isinstance(value, dict) and isinstance(merged.get(key), dict):
            merged[key] = _deep_merge(merged[key], value)
        else:
            merged[key] = value
    return merged


class ConfigPatchManager:
    """Apply YAML patches to config files with automatic backup/rollback support."""

    def __init__(self, config_path: str, backup_dir: Optional[str] = None):
        self.config_path = Path(config_path)
        self.backup_dir = Path(backup_dir) if backup_dir else self.config_path.parent / ".trading-config-backups"
        self.backup_dir.mkdir(parents=True, exist_ok=True)

    def _timestamped_backup_path(self, reason: str) -> Path:
        """Return a backup file path under the backup directory."""
        stamp = datetime.now().strftime("%Y%m%d-%H%M%S")
        stem = self.config_path.stem
        suffix = self.config_path.suffix or ".yaml"
        return self.backup_dir / f"{stem}-{reason}-{stamp}{suffix}"

    def load_config_dict(self) -> dict[str, Any]:
        """Load the current config YAML as a dict."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        with open(self.config_path, encoding="utf-8") as f:
            return yaml.safe_load(f) or {}

    def backup_current(self, reason: str = "backup") -> Path:
        """Copy the current config file to backup storage and return the backup path."""
        if not self.config_path.exists():
            raise FileNotFoundError(f"Config file not found: {self.config_path}")
        backup_path = self._timestamped_backup_path(reason)
        shutil.copy2(self.config_path, backup_path)
        return backup_path

    def list_backups(self) -> list[Path]:
        """Return available config backups sorted newest-first."""
        return sorted(self.backup_dir.glob(f"{self.config_path.stem}-*{self.config_path.suffix or '.yaml'}"), reverse=True)

    def write_config_dict(self, data: dict[str, Any]) -> None:
        """Persist a config dict to the config file."""
        self.config_path.write_text(
            yaml.safe_dump(data, allow_unicode=True, sort_keys=False),
            encoding="utf-8",
        )

    def apply_patch(self, patch: dict[str, Any], reason: str = "apply") -> tuple[Path, Path]:
        """Backup current config, apply patch, and return (backup_path, config_path)."""
        current = self.load_config_dict()
        merged = _deep_merge(current, patch)
        backup_path = self.backup_current(reason=reason)
        self.write_config_dict(merged)
        return backup_path, self.config_path

    def rollback(self, backup_path: Optional[str] = None) -> tuple[Path, Path]:
        """Restore config from a backup, creating a safety backup of the current file first."""
        available = self.list_backups()
        if backup_path:
            target = Path(backup_path)
        else:
            if not available:
                raise FileNotFoundError("No config backups available for rollback.")
            target = available[0]
        if not target.exists():
            raise FileNotFoundError(f"Backup file not found: {target}")

        safety_backup = self.backup_current(reason="pre-rollback")
        shutil.copy2(target, self.config_path)
        return target, safety_backup
