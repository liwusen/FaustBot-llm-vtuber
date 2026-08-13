from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import faust_backend.config_loader as conf


UI_SETTINGS_PATH = Path(conf.CONFIG_ROOT) / "ui-settings.json"
DEFAULT_UI_SETTINGS: dict[str, Any] = {"widgets": {}}


def _normalize_settings(payload: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """只保留已知顶层键：widgets 必存；onboarding 仅当 payload 提供时写入，
    否则从 existing（磁盘现有内容）合并保留，防止只 POST widgets 时丢标志。"""
    if not isinstance(payload, dict):
        payload = {}
    widgets = payload.get("widgets")
    if not isinstance(widgets, dict):
        widgets = {}
    result: dict[str, Any] = {"widgets": widgets}

    onboarding = payload.get("onboarding")
    if not isinstance(onboarding, dict):
        onboarding = None
        if isinstance(existing, dict):
            candidate = existing.get("onboarding")
            if isinstance(candidate, dict):
                onboarding = candidate
    if onboarding is not None:
        result["onboarding"] = onboarding
    return result


def load_ui_settings() -> dict[str, Any]:
    path = UI_SETTINGS_PATH
    if not path.exists():
        return dict(DEFAULT_UI_SETTINGS)
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return dict(DEFAULT_UI_SETTINGS)
    return _normalize_settings(payload)


def save_ui_settings(payload: Any) -> dict[str, Any]:
    existing = load_ui_settings()
    normalized = _normalize_settings(payload, existing)
    UI_SETTINGS_PATH.parent.mkdir(parents=True, exist_ok=True)
    UI_SETTINGS_PATH.write_text(
        json.dumps(normalized, ensure_ascii=False, indent=2),
        encoding="utf-8",
    )
    return normalized
