"""Tests for V5 download/upgrade helpers; no network access."""

from __future__ import annotations

import zipfile
from pathlib import Path

import pytest

import download_tts
from faust_backend.services import tts_install


def _make_v5_root(root: Path) -> Path:
    (root / "GPT_SoVITS" / "module").mkdir(parents=True, exist_ok=True)
    (root / "GPT_SoVITS" / "pretrained_models" / "gsv-v5-pretrained").mkdir(
        parents=True, exist_ok=True
    )
    (root / "api.py").write_text("", encoding="utf-8")
    (root / tts_install.V5_MODELS_MODULE).write_text("", encoding="utf-8")
    (root / tts_install.V5_TURBO_WEIGHT).write_text("", encoding="utf-8")
    (root / tts_install.V5_GPT_WEIGHT).write_text("", encoding="utf-8")
    return root


def test_variant_urls_are_v5() -> None:
    standard = download_tts.choose_download_by_variant("standard")
    nvidia = download_tts.choose_download_by_variant("nvidia50")

    assert "GPT-SoVITS-v5-20261005.7z" in standard[0]
    assert standard[1] == "GPT-SoVITS-v5-20261005.7z"
    assert "nvidia50x0" in nvidia[0]
    assert nvidia[1] == "GPT-SoVITS-v5-20261005nvidia50x0.7z"


def test_normalize_tts_layout_moves_nested_package(monkeypatch, tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    nested = hub / "GPT-SoVITS-v5" / "GPT-SoVITS-v5"
    nested.mkdir(parents=True)
    (nested / "api.py").write_text("", encoding="utf-8")
    (nested / "marker.txt").write_text("x", encoding="utf-8")
    monkeypatch.setattr(download_tts, "TTS_HUB_DIR", hub)

    download_tts.normalize_tts_layout()

    assert (hub / "GPT-SoVITS-v5" / "api.py").is_file()
    assert (hub / "GPT-SoVITS-v5" / "marker.txt").is_file()


def test_normalize_tts_layout_leaves_flat_package_untouched(monkeypatch, tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    flat = hub / "GPT-SoVITS-v5"
    flat.mkdir(parents=True)
    (flat / "api.py").write_text("", encoding="utf-8")
    monkeypatch.setattr(download_tts, "TTS_HUB_DIR", hub)

    download_tts.normalize_tts_layout()

    assert (flat / "api.py").is_file()


def test_verify_v5_install_accepts_complete_root(tmp_path: Path) -> None:
    root = _make_v5_root(tmp_path / "install")

    download_tts.verify_v5_install(root)


def test_verify_v5_install_rejects_missing_weights(tmp_path: Path) -> None:
    root = _make_v5_root(tmp_path / "install")
    (root / tts_install.V5_TURBO_WEIGHT).unlink()

    with pytest.raises(RuntimeError, match="V5 安装校验失败"):
        download_tts.verify_v5_install(root)


def test_safe_zip_target_rejects_traversal(tmp_path: Path) -> None:
    with pytest.raises(RuntimeError, match="非法路径"):
        download_tts._safe_zip_target(tmp_path, "../evil.txt")
    with pytest.raises(RuntimeError, match="非法路径"):
        download_tts._safe_zip_target(tmp_path, "C:/Windows/evil.txt")


def test_extract_zip_over_rolls_back_on_error(tmp_path: Path) -> None:
    root = tmp_path / "install"
    root.mkdir()
    (root / "api.py").write_text("old", encoding="utf-8")
    archive_path = tmp_path / "update.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("api.py", "new")
        archive.writestr("../evil.txt", "bad")

    with pytest.raises(RuntimeError, match="非法路径"):
        download_tts.extract_zip_over(archive_path, root)

    assert (root / "api.py").read_text(encoding="utf-8") == "old"
    assert not (tmp_path / "evil.txt").exists()


def test_extract_zip_over_writes_files_and_keeps_backup(tmp_path: Path) -> None:
    root = tmp_path / "install"
    root.mkdir()
    (root / "api.py").write_text("old", encoding="utf-8")
    archive_path = tmp_path / "update.zip"
    with zipfile.ZipFile(archive_path, "w") as archive:
        archive.writestr("api.py", "new")
        archive.writestr("GPT_SoVITS/module/models_v5.py", "code")

    download_tts.extract_zip_over(archive_path, root)

    assert (root / "api.py").read_text(encoding="utf-8") == "new"
    assert (root / tts_install.V5_MODELS_MODULE).read_text(encoding="utf-8") == "code"
    assert (root / ".v5-upgrade-backup" / "api.py").read_text(encoding="utf-8") == "old"


@pytest.mark.asyncio
async def test_upgrade_rejects_existing_v5(monkeypatch, tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    _make_v5_root(hub / "GPT-SoVITS-v5")
    monkeypatch.setattr(download_tts, "TTS_HUB_DIR", hub)

    with pytest.raises(RuntimeError, match="已是 V5"):
        await download_tts.download_tts_async("standard", action="upgrade")


@pytest.mark.asyncio
async def test_download_async_rejects_unknown_action() -> None:
    with pytest.raises(ValueError, match="未知 TTS 安装动作"):
        await download_tts.download_tts_async("standard", action="remove")
