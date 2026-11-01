from __future__ import annotations

import json
from pathlib import Path
from typing import Any

import faust_backend.core.config as conf


UI_SETTINGS_PATH = Path(conf.CONFIG_ROOT) / "ui-settings.json"
DEFAULT_UI_SETTINGS: dict[str, Any] = {"widgets": {}}
VNG_SETTINGS_KEY = "vng"


def _normalize_settings(payload: Any, existing: dict[str, Any] | None = None) -> dict[str, Any]:
    """只保留已知顶层键，并把「部分写入」安全地合并到磁盘现有内容上。

    - widgets：payload 未提供时回退到 existing，避免只写 vng/onboarding 时清空布局；
    - onboarding：payload 未提供时沿用 existing（显式传 {} 表示清空）；
    - vng：payload 未提供时沿用 existing；存在时统一归一化为 {"enabled": bool}。
    """
    if not isinstance(payload, dict):
        payload = {}
    existing = existing if isinstance(existing, dict) else {}

    widgets = payload.get("widgets")
    if not isinstance(widgets, dict):
        existing_widgets = existing.get("widgets")
        widgets = existing_widgets if isinstance(existing_widgets, dict) else {}
    result: dict[str, Any] = {"widgets": widgets}

    onboarding = payload.get("onboarding")
    if not isinstance(onboarding, dict):
        onboarding = None
        candidate = existing.get("onboarding")
        if isinstance(candidate, dict):
            onboarding = candidate
    if onboarding is not None:
        result["onboarding"] = onboarding

    vng = payload.get(VNG_SETTINGS_KEY)
    if not isinstance(vng, dict):
        candidate = existing.get(VNG_SETTINGS_KEY)
        vng = candidate if isinstance(candidate, dict) else None
    if isinstance(vng, dict):
        result[VNG_SETTINGS_KEY] = {"enabled": bool(vng.get("enabled", False))}
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
    if normalized.get("widgets") != (existing.get("widgets") or {}):
        # UI 参数实际变化：通知前端立即重载（懒导入避免加载环）
        from faust_backend.core.frontend.api import FrontEndReloadSettings

        FrontEndReloadSettings()
    return normalized


def get_vng_enabled() -> bool:
    """VNG（视觉小说）模式是否开启；未写入过该键时视为关闭。"""
    settings = load_ui_settings()
    vng = settings.get(VNG_SETTINGS_KEY)
    return bool(isinstance(vng, dict) and vng.get("enabled"))


def set_vng_enabled(enabled: bool) -> dict[str, Any]:
    """只更新 vng.enabled，保留 widgets / onboarding 不动。"""
    current = load_ui_settings()
    current[VNG_SETTINGS_KEY] = {"enabled": bool(enabled)}
    return save_ui_settings(current)
