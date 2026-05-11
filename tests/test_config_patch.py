"""Tests for safe config patch apply / rollback helpers."""

from pathlib import Path

import yaml

from trading.core.config_patch import ConfigPatchManager


def _write_config(path: Path, data: dict) -> None:
    path.write_text(yaml.safe_dump(data, allow_unicode=True, sort_keys=False), encoding="utf-8")


def test_apply_patch_merges_and_creates_backup(tmp_path):
    config_path = tmp_path / "config.yaml"
    _write_config(
        config_path,
        {
            "risk": {"max_leverage": 35, "meme_max_leverage": 2},
            "events": {"high_impact_max_leverage": 10},
        },
    )
    mgr = ConfigPatchManager(str(config_path))

    backup_path, updated = mgr.apply_patch(
        {
            "risk": {"meme_max_leverage": 1},
            "events": {"high_impact_max_leverage": 8},
        },
        reason="test",
    )
    assert updated == config_path
    assert backup_path.exists()
    merged = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert merged["risk"]["max_leverage"] == 35
    assert merged["risk"]["meme_max_leverage"] == 1
    assert merged["events"]["high_impact_max_leverage"] == 8


def test_rollback_restores_previous_config(tmp_path):
    config_path = tmp_path / "config.yaml"
    original = {"risk": {"meme_max_leverage": 2}}
    _write_config(config_path, original)
    mgr = ConfigPatchManager(str(config_path))
    backup_path, _ = mgr.apply_patch({"risk": {"meme_max_leverage": 1}}, reason="apply")
    modified = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert modified["risk"]["meme_max_leverage"] == 1

    restored_from, safety_backup = mgr.rollback(str(backup_path))
    restored = yaml.safe_load(config_path.read_text(encoding="utf-8"))
    assert restored_from == backup_path
    assert safety_backup.exists()
    assert restored["risk"]["meme_max_leverage"] == 2
