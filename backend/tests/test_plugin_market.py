import json
import sys
import zipfile
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

sys.argv = [sys.argv[0]]

import faust_backend.plugin_market as plugin_market
from faust_backend.update_manager import is_newer_version


def _catalog_payload(plugins):
    return {"updated_at": "2026-01-01T00:00:00Z", "plugins": plugins}


def test_is_newer_version_boundaries():
    assert is_newer_version("1.0.1", "1.0.0")
    assert is_newer_version("1.10.0", "1.9.0")
    assert is_newer_version("2.0", "1.9.9")
    assert is_newer_version("v1.1", "1.0")
    assert not is_newer_version("1.0.0", "1.0.0")
    assert not is_newer_version("1.0.0", "1.0.1")


def test_fetch_catalog_normalizes_and_filters(monkeypatch):
    raw = _catalog_payload([
        {"id": "good", "name": "Good", "version": "1.0.0",
         "download_url": "https://example.com/good.zip", "tags": ["a"]},
        {"id": "no-download", "name": "Bad", "version": "1.0.0"},
        {"id": "", "download_url": "https://example.com/x.zip"},
        "not-a-dict",
    ])
    monkeypatch.setattr(plugin_market, "_fetch_json", lambda url, **kw: raw)

    catalog = plugin_market.fetch_catalog()
    assert catalog["index_url"] == plugin_market.MARKET_INDEX_URL
    assert catalog["updated_at"] == "2026-01-01T00:00:00Z"
    ids = [p["id"] for p in catalog["plugins"]]
    assert ids == ["good"]
    entry = catalog["plugins"][0]
    assert "repo" not in entry and "release_url" not in entry
    assert entry["download_url"] == "https://example.com/good.zip"


def test_fetch_catalog_rejects_bad_shape(monkeypatch):
    monkeypatch.setattr(plugin_market, "_fetch_json", lambda url, **kw: {"plugins": "nope"})
    with pytest.raises(plugin_market.PluginMarketError):
        plugin_market.fetch_catalog()


def _make_plugin_zip(path: Path, plugin_id: str, version: str = "1.0.0", extra_files=None):
    manifest = {"id": plugin_id, "name": plugin_id, "version": version, "entry": "main.py"}
    with zipfile.ZipFile(path, "w") as zf:
        zf.writestr(f"{plugin_id}/plugin.json", json.dumps(manifest))
        zf.writestr(f"{plugin_id}/main.py", "def get_plugin():\n    return None\n")
        for name, content in (extra_files or {}).items():
            zf.writestr(f"{plugin_id}/{name}", content)


def _patch_market(monkeypatch, tmp_path, plugin_id, zip_builder):
    catalog = {
        "index_url": plugin_market.MARKET_INDEX_URL,
        "updated_at": "2026-01-01T00:00:00Z",
        "plugins": [{
            "id": plugin_id, "name": plugin_id, "description": "", "author": "",
            "version": "1.0.0", "download_url": "https://example.com/p.zip",
            "homepage": "", "tags": [],
        }],
    }
    monkeypatch.setattr(plugin_market, "fetch_catalog", lambda: catalog)

    def fake_download(url, target_file, timeout=60.0):
        zip_builder(target_file)
        return target_file.stat().st_size

    monkeypatch.setattr(plugin_market, "_download_file", fake_download)


def test_sync_plugin_overwrites_existing(monkeypatch, tmp_path):
    plugins_dir = tmp_path / "plugins"
    old_dir = plugins_dir / "demo"
    old_dir.mkdir(parents=True)
    (old_dir / "stale.py").write_text("old", encoding="utf-8")

    _patch_market(monkeypatch, tmp_path, "demo",
                  lambda p: _make_plugin_zip(p, "demo", extra_files={"new.txt": "new"}))

    result = plugin_market.sync_plugin_from_catalog(plugin_id="demo", plugins_dir=plugins_dir)
    assert result["plugin_id"] == "demo"
    assert not (old_dir / "stale.py").exists()
    assert (old_dir / "plugin.json").exists()
    assert (old_dir / "new.txt").read_text(encoding="utf-8") == "new"


def test_sync_plugin_id_mismatch(monkeypatch, tmp_path):
    plugins_dir = tmp_path / "plugins"
    _patch_market(monkeypatch, tmp_path, "demo",
                  lambda p: _make_plugin_zip(p, "other"))
    with pytest.raises(plugin_market.PluginMarketError):
        plugin_market.sync_plugin_from_catalog(plugin_id="demo", plugins_dir=plugins_dir)
    assert not (plugins_dir / "demo").exists()


def test_sync_plugin_not_in_market(monkeypatch, tmp_path):
    monkeypatch.setattr(plugin_market, "fetch_catalog", lambda: {
        "index_url": plugin_market.MARKET_INDEX_URL, "updated_at": None, "plugins": []})
    with pytest.raises(plugin_market.PluginMarketError):
        plugin_market.sync_plugin_from_catalog(plugin_id="ghost", plugins_dir=tmp_path)


def test_sync_plugin_rejects_bad_id(tmp_path):
    with pytest.raises(plugin_market.PluginMarketError):
        plugin_market.sync_plugin_from_catalog(plugin_id="../evil", plugins_dir=tmp_path)


def test_apply_gh_proxy_toggle(monkeypatch):
    url = "https://raw.githubusercontent.com/liwusen/FaustBotPluginMarket/main/plugins.json"
    monkeypatch.setattr(plugin_market.conf, "PLUGIN_MARKET_USE_GH_PROXY", False)
    assert plugin_market._apply_gh_proxy(url) == url

    monkeypatch.setattr(plugin_market.conf, "PLUGIN_MARKET_USE_GH_PROXY", True)
    assert plugin_market._apply_gh_proxy(url) == f"{plugin_market.GH_PROXY}/{url}"
    release = "https://github.com/liwusen/FaustBotPluginMarket/releases/download/x/y.zip"
    assert plugin_market._apply_gh_proxy(release) == f"{plugin_market.GH_PROXY}/{release}"
    other = "https://example.com/p.zip"
    assert plugin_market._apply_gh_proxy(other) == other


def test_check_plugin_updates_classification(monkeypatch):
    monkeypatch.setattr(plugin_market, "fetch_catalog", lambda: {
        "index_url": plugin_market.MARKET_INDEX_URL,
        "updated_at": "2026-01-01T00:00:00Z",
        "plugins": [
            {"id": "upd", "name": "Updatable", "version": "2.0.0", "download_url": "https://x/u.zip"},
            {"id": "same", "name": "Same", "version": "1.0.0", "download_url": "https://x/s.zip"},
        ],
    })
    installed = [
        {"id": "upd", "version": "1.0.0"},
        {"id": "same", "version": "1.0.0"},
        {"id": "local-only", "version": "0.1"},
    ]
    result = plugin_market.check_plugin_updates(installed)
    assert [u["id"] for u in result["updates"]] == ["upd"]
    assert result["updates"][0]["latest_version"] == "2.0.0"
    assert result["updates"][0]["installed_version"] == "1.0.0"
    assert result["up_to_date"] == ["same"]
    assert result["not_in_market"] == ["local-only"]
