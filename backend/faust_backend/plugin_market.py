from __future__ import annotations

import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

from tqdm import tqdm

import requests

import faust_backend.config_loader as conf
from faust_backend.update_manager import GH_PROXY, is_newer_version

from faust_backend.logger import get_logger

log = get_logger("faust.plugin_market")

MARKET_INDEX_URL = "https://raw.githubusercontent.com/liwusen/FaustBotPluginMarket/main/plugins.json"
_SAFE_PLUGIN_ID = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")
_GH_PROXYABLE_HOSTS = ("https://raw.githubusercontent.com/", "https://github.com/")


def _apply_gh_proxy(url: str) -> str:
    if not conf.PLUGIN_MARKET_USE_GH_PROXY:
        return url
    if url.startswith(_GH_PROXYABLE_HOSTS):
        return f"{GH_PROXY}/{url}"
    return url


class PluginMarketError(RuntimeError):
    pass


class PluginAlreadyInstalledError(PluginMarketError):
    pass


def _fetch_json(url: str, *, timeout: float = 20.0) -> dict[str, Any]:
    resp = requests.get(url, timeout=timeout)
    resp.raise_for_status()
    data = resp.json()
    if not isinstance(data, dict):
        raise PluginMarketError(f"插件市场元数据格式错误: 顶层必须是对象, url={url}")
    return data


def fetch_catalog() -> dict[str, Any]:
    raw = _fetch_json(_apply_gh_proxy(MARKET_INDEX_URL))
    items = raw.get("plugins")
    if not isinstance(items, list):
        raise PluginMarketError("插件市场元数据格式错误: 缺少 plugins 列表")
    normalized: list[dict[str, Any]] = []
    for row in items:
        if not isinstance(row, dict):
            continue
        plugin_id = str(row.get("id") or "").strip()
        if not plugin_id:
            continue
        download_url = str(row.get("download_url") or "").strip()
        if not download_url:
            continue
        normalized.append(
            {
                "id": plugin_id,
                "name": str(row.get("name") or plugin_id),
                "description": str(row.get("description") or ""),
                "author": str(row.get("author") or ""),
                "version": str(row.get("version") or ""),
                "download_url": download_url,
                "homepage": str(row.get("homepage") or ""),
                "tags": list(row.get("tags") or []),
            }
        )
    return {
        "index_url": MARKET_INDEX_URL,
        "updated_at": raw.get("updated_at"),
        "plugins": normalized,
    }


def _resolve_download_url(plugin_entry: dict[str, Any]) -> str:
    direct = str(plugin_entry.get("download_url") or "").strip()
    if not direct:
        raise PluginMarketError(f"插件 {plugin_entry.get('id')} 缺少 download_url")
    return direct


def _download_file(url: str, target_file: Path, timeout: float = 60.0) -> int:
    with requests.get(url, stream=True, timeout=timeout) as resp:
        resp.raise_for_status()
        total = 0
        with target_file.open("wb") as f:
            for chunk in tqdm(resp.iter_content(chunk_size=1024 * 128), desc=f"Downloading {target_file.name}", unit="B", unit_scale=True):
                if not chunk:
                    continue
                f.write(chunk)
                total += len(chunk)
        return total


def _find_plugin_root(extract_dir: Path, plugin_id: str) -> Path:
    candidates = [
        p.parent for p in extract_dir.rglob("plugin.json")
        if p.is_file() and "__MACOSX" not in p.parts
    ]

    if not candidates:
        children = [p for p in extract_dir.iterdir() if p.is_dir()]
        if len(children) == 1:
            return children[0]
        raise PluginMarketError("压缩包中未找到 plugin.json，无法判定插件目录")

    if len(candidates) == 1:
        return candidates[0]

    for c in candidates:
        if c.name == plugin_id:
            return c

    raise PluginMarketError("压缩包中存在多个 plugin.json，无法唯一识别插件目录")


def sync_plugin_from_catalog(
    *,
    plugin_id: str,
    plugins_dir: Path,
) -> dict[str, Any]:
    plugin_id = str(plugin_id or "").strip()
    if not _SAFE_PLUGIN_ID.match(plugin_id):
        raise PluginMarketError(f"非法插件 ID: {plugin_id}")
    log.debug(f"开始从插件市场同步插件: {plugin_id}")
    catalog = fetch_catalog()
    target_entry = None
    for item in catalog["plugins"]:
        if item.get("id") == plugin_id:
            target_entry = item
            break
    if not target_entry:
        raise PluginMarketError(f"插件市场中未找到插件: {plugin_id}")

    download_url = _resolve_download_url(target_entry)

    plugins_dir = Path(plugins_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)
    target_dir = plugins_dir / plugin_id
    log.debug(f"开始下载: {plugin_id}")
    with tempfile.TemporaryDirectory(prefix=f"faust-plugin-{plugin_id}-") as td:
        tmp_dir = Path(td)
        zip_file = tmp_dir / "plugin_pack.zip"
        extract_dir = tmp_dir / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        size_bytes = _download_file(_apply_gh_proxy(download_url), zip_file)
        log.debug(f"下载完成: {plugin_id}, 大小 {size_bytes} 字节, 开始解压")
        try:
            with zipfile.ZipFile(zip_file, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise PluginMarketError("下载的插件包不是有效 zip 文件") from exc
        log.debug(f"解压完成: {plugin_id}, 开始安装到 {target_dir}")

        plugin_root = _find_plugin_root(extract_dir, plugin_id)
        manifest_file = plugin_root / "plugin.json"
        if not manifest_file.exists():
            raise PluginMarketError("插件包缺少 plugin.json")

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        manifest_id = str(manifest.get("id") or plugin_root.name)
        if manifest_id != plugin_id:
            raise PluginMarketError(
                f"插件包 ID 不匹配: 期望 {plugin_id}, 实际 {manifest_id}"
            )

        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(plugin_root, target_dir)
        log.debug(f"安装完成: {plugin_id}, 安装目录 {target_dir}")

    return {
        "plugin_id": plugin_id,
        "install_dir": str(target_dir),
        "market": {
            "index_url": catalog.get("index_url"),
            "entry": target_entry,
        },
        "download": {
            "url": download_url,
            "size_bytes": size_bytes,
        },
    }


def check_plugin_updates(installed: list[dict[str, Any]]) -> dict[str, Any]:
    catalog = fetch_catalog()
    market_by_id = {p["id"]: p for p in catalog["plugins"]}

    updates: list[dict[str, Any]] = []
    up_to_date: list[str] = []
    not_in_market: list[str] = []
    for row in installed:
        pid = str(row.get("id") or "").strip()
        if not pid:
            continue
        entry = market_by_id.get(pid)
        if entry is None:
            not_in_market.append(pid)
            continue
        installed_version = str(row.get("version") or "")
        latest_version = str(entry.get("version") or "")
        if latest_version and is_newer_version(latest_version, installed_version):
            updates.append(
                {
                    "id": pid,
                    "name": entry.get("name") or pid,
                    "installed_version": installed_version,
                    "latest_version": latest_version,
                }
            )
        else:
            up_to_date.append(pid)

    return {
        "index_url": catalog.get("index_url"),
        "updated_at": catalog.get("updated_at"),
        "updates": updates,
        "up_to_date": up_to_date,
        "not_in_market": not_in_market,
    }


def install_plugin_from_zip(
    *,
    zip_path: str,
    plugins_dir: Path,
    overwrite: bool = False,
    expected_plugin_id: str | None = None,
) -> dict[str, Any]:
    src_zip = Path(str(zip_path or "").strip())
    if not src_zip.exists() or not src_zip.is_file():
        raise PluginMarketError(f"ZIP 文件不存在: {src_zip}")

    plugins_dir = Path(plugins_dir)
    plugins_dir.mkdir(parents=True, exist_ok=True)

    expected_id = str(expected_plugin_id or "").strip()
    if expected_id and not _SAFE_PLUGIN_ID.match(expected_id):
        raise PluginMarketError(f"非法插件 ID: {expected_id}")

    with tempfile.TemporaryDirectory(prefix="faust-plugin-local-") as td:
        extract_dir = Path(td) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(src_zip, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise PluginMarketError("本地 ZIP 不是有效压缩包") from exc

        plugin_root = _find_plugin_root(extract_dir, expected_id or "")
        manifest_file = plugin_root / "plugin.json"
        if not manifest_file.exists():
            raise PluginMarketError("插件包缺少 plugin.json")

        manifest = json.loads(manifest_file.read_text(encoding="utf-8"))
        plugin_id = str(manifest.get("id") or plugin_root.name).strip()
        if not _SAFE_PLUGIN_ID.match(plugin_id):
            raise PluginMarketError(f"插件包中的 ID 非法: {plugin_id}")
        if expected_id and plugin_id != expected_id:
            raise PluginMarketError(f"插件 ID 不匹配: 期望 {expected_id}, 实际 {plugin_id}")

        target_dir = plugins_dir / plugin_id
        if target_dir.exists() and not overwrite:
            raise PluginAlreadyInstalledError(f"插件 {plugin_id} 已安装，需确认覆盖后才能继续")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(plugin_root, target_dir)

    return {
        "plugin_id": plugin_id,
        "install_dir": str(target_dir),
        "source": {
            "type": "local_zip",
            "zip_path": str(src_zip.resolve()),
        },
    }


def package_plugin_to_zip(
    *,
    plugin_id: str,
    plugins_dir: Path,
    output_dir: Path | None = None,
    zip_name: str | None = None,
) -> dict[str, Any]:
    pid = str(plugin_id or "").strip()
    if not _SAFE_PLUGIN_ID.match(pid):
        raise PluginMarketError(f"非法插件 ID: {pid}")

    plugins_dir = Path(plugins_dir)
    plugin_dir = plugins_dir / pid
    if not plugin_dir.exists() or not plugin_dir.is_dir():
        raise PluginMarketError(f"插件不存在: {pid}")
    if not (plugin_dir / "plugin.json").exists():
        raise PluginMarketError(f"插件 {pid} 缺少 plugin.json")

    out_dir = Path(output_dir) if output_dir else plugins_dir / "_dist"
    out_dir.mkdir(parents=True, exist_ok=True)
    out_name = str(zip_name or f"{pid}.zip").strip() or f"{pid}.zip"
    if not out_name.lower().endswith(".zip"):
        out_name += ".zip"
    zip_file = out_dir / out_name

    with zipfile.ZipFile(zip_file, "w", compression=zipfile.ZIP_DEFLATED) as zf:
        for p in plugin_dir.rglob("*"):
            if p.is_dir():
                continue
            if "__pycache__" in p.parts:
                continue
            rel = p.relative_to(plugin_dir)
            arc = Path(pid) / rel
            zf.write(p, str(arc).replace("\\", "/"))

    return {
        "plugin_id": pid,
        "zip_path": str(zip_file.resolve()),
        "output_dir": str(out_dir.resolve()),
    }


def delete_installed_plugin(*, plugin_id: str, plugins_dir: Path, state_file: Path | None = None) -> dict[str, Any]:
    pid = str(plugin_id or "").strip()
    if not _SAFE_PLUGIN_ID.match(pid):
        raise PluginMarketError(f"非法插件 ID: {pid}")

    plugins_dir = Path(plugins_dir)
    target_dir = plugins_dir / pid
    if not target_dir.exists() or not target_dir.is_dir():
        raise PluginMarketError(f"插件不存在: {pid}")

    shutil.rmtree(target_dir)

    sf = Path(state_file) if state_file else plugins_dir / "plugins.state.json"
    if sf.exists():
        try:
            raw = json.loads(sf.read_text(encoding="utf-8"))
            if isinstance(raw, dict):
                plugins_state = raw.get("plugins")
                if isinstance(plugins_state, dict):
                    plugins_state.pop(pid, None)
                configs_state = raw.get("configs")
                if isinstance(configs_state, dict):
                    configs_state.pop(pid, None)
                sf.write_text(json.dumps(raw, ensure_ascii=False, indent=2), encoding="utf-8")
        except Exception:
            pass

    return {
        "plugin_id": pid,
        "deleted_dir": str(target_dir),
    }
