"""Tests for GPT-SoVITS install discovery and V5 weight resolution."""

from __future__ import annotations

from pathlib import Path

import pytest

from faust_backend.services import tts_install


def _make_install(
    root: Path,
    *,
    api: bool = True,
    v5_code: bool = True,
    models: tuple[str, ...] = ("v5turbo",),
    gpt: bool = True,
) -> Path:
    (root / "GPT_SoVITS" / "module").mkdir(parents=True, exist_ok=True)
    (root / "GPT_SoVITS" / "pretrained_models" / "gsv-v5-pretrained").mkdir(
        parents=True, exist_ok=True
    )
    if api:
        (root / "api.py").write_text("", encoding="utf-8")
    if v5_code:
        (root / "GPT_SoVITS" / "module" / "models_v5.py").write_text("", encoding="utf-8")
    if "v5turbo" in models:
        (root / tts_install.V5_TURBO_WEIGHT).write_text("", encoding="utf-8")
    if "v5dev" in models:
        (root / tts_install.V5_DEV_WEIGHT).write_text("", encoding="utf-8")
    if gpt:
        (root / tts_install.V5_GPT_WEIGHT).write_text("", encoding="utf-8")
    return root


def test_find_prefers_v5_over_legacy_bundle(tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    _make_install(hub / "GPT-SoVITS-Bundle", v5_code=False, models=())
    _make_install(hub / "GPT-SoVITS-v5-20261005")

    install = tts_install.find_tts_install(hub)

    assert install is not None
    assert install.root.name == "GPT-SoVITS-v5-20261005"
    assert install.version == "v5"
    assert install.is_v5 is True
    assert install.variant == "standard"
    assert install.available_models == ("v5turbo",)


def test_find_supports_nested_layout_and_nvidia_variant(tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    root = hub / "GPT-SoVITS-v5-20261005nvidia50x0" / "GPT-SoVITS-v5-20261005nvidia50x0"
    _make_install(root)

    install = tts_install.find_tts_install(hub)

    assert install is not None
    assert install.root == root
    assert install.variant == "nvidia50"


def test_find_skips_dirs_without_api(tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    _make_install(hub / "GPT-SoVITS-v5-20261005", api=False)

    assert tts_install.find_tts_install(hub) is None


def test_v5_code_without_weights_is_incomplete(tmp_path: Path) -> None:
    hub = tmp_path / "tts-hub"
    _make_install(hub / "GPT-SoVITS-v5-20261005", models=())

    install = tts_install.find_tts_install(hub)

    assert install is not None
    assert install.version == "v5-incomplete"
    assert install.is_v5 is False
    assert install.available_models == ()


def test_v5_weight_paths_falls_back_to_turbo(tmp_path: Path) -> None:
    root = _make_install(tmp_path / "GPT-SoVITS-v5", models=("v5turbo",))
    install = tts_install.find_tts_install(tmp_path)

    assert install is not None
    sovits, gpt = tts_install.v5_weight_paths(install, "v5dev")

    assert sovits == root / tts_install.V5_TURBO_WEIGHT
    assert gpt == root / tts_install.V5_GPT_WEIGHT
    assert sovits.is_absolute() and gpt.is_absolute()


def test_v5_weight_paths_uses_dev_when_present(tmp_path: Path) -> None:
    root = _make_install(tmp_path / "GPT-SoVITS-v5", models=("v5turbo", "v5dev"))
    install = tts_install.find_tts_install(tmp_path)

    assert install is not None
    sovits, _gpt = tts_install.v5_weight_paths(install, "v5dev")

    assert sovits == root / tts_install.V5_DEV_WEIGHT


def test_v5_weight_paths_raises_when_all_weights_missing(tmp_path: Path) -> None:
    root = _make_install(tmp_path / "GPT-SoVITS-v5", models=())
    install = tts_install.find_tts_install(tmp_path)

    assert install is not None
    with pytest.raises(FileNotFoundError, match="SoVITS 权重缺失"):
        tts_install.v5_weight_paths(install, "v5turbo")


def test_tts_status_reports_version_and_models(tmp_path: Path) -> None:
    _make_install(tmp_path / "GPT-SoVITS-v5-20261005", models=("v5turbo", "v5dev"))

    status = tts_install.tts_status(tmp_path)

    assert status["installed"] is True
    assert status["version"] == "v5"
    assert status["is_v5"] is True
    assert status["available_models"] == ["v5turbo", "v5dev"]
