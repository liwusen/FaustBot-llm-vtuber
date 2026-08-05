import asyncio
import concurrent.futures
import json
import os
import re
import shutil
import subprocess
import sys
import tempfile
import threading
import time
import zipfile
from datetime import datetime
from pathlib import Path
from typing import Any, Callable

import requests

import faust_backend.logger as logger
from faust_backend.utils import DownloadTask

log = logger.get_logger("faust.update.manager")

GITHUB_OWNER = "liwusen"
GITHUB_REPO = "FaustBot-llm-vtuber"
GH_PROXY = "https://gh-proxy.org"
VERSION_FILE = "version.json"
RELEASE_CACHE_HOURS = 1
DOWNLOAD_THREADS = 32
DOWNLOAD_CHUNK_SIZE = 8192 * 16
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_RETRY_BASE_DELAY = 2.0

PRESERVE_PATTERNS = [
    ".runtime/",
    ".nodejs/",
    "backend/tts-hub/",
    "backend/asr-hub/",
    "backend/voices/",
    "logs/",
    "*.private.json",
    "*.log",
    "*.pyc",
]


def is_newer_version(new_tag: str, old_tag: str) -> bool:
    def _parts(t: str) -> tuple:
        cleaned = str(t or "").lstrip("Vv")
        parts = []
        for seg in re.split(r"[._\-]", cleaned):
            try:
                parts.append(int(seg))
            except ValueError:
                parts.append(seg)
        return tuple(parts)

    try:
        return _parts(new_tag) > _parts(old_tag)
    except TypeError:
        # 混合 int/str 段无法比较（版本号格式异常），退化为字符串比较
        return str(new_tag) > str(old_tag)


def _normalize_tag(tag: str) -> str:
    return tag.lstrip("Vv")


def _load_version(project_root: Path) -> dict[str, str]:
    path = project_root / "backend" / VERSION_FILE
    if path.exists():
        try:
            return json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            pass
    return {"version": "0.0.0", "tag": "V0.0.0"}


def _current_tag(project_root: Path) -> str:
    return str(_load_version(project_root).get("tag", "V0.0.0"))


def _should_preserve(rel_path: str) -> bool:
    norm = rel_path.replace("\\", "/")
    for pat in PRESERVE_PATTERNS:
        if pat.endswith("/"):
            if norm.startswith(pat) or norm == pat.rstrip("/"):
                return True
        elif pat.startswith("*."):
            if norm.endswith(pat.lstrip("*")):
                return True
        elif norm == pat:
            return True
    return False


def _gh_api_url(endpoint: str) -> str:
    return f"https://api.github.com/repos/{GITHUB_OWNER}/{GITHUB_REPO}/{endpoint.lstrip('/')}"


def _gh_download_url(tag: str, asset_name: str, use_proxy: bool) -> str:
    raw = f"https://github.com/{GITHUB_OWNER}/{GITHUB_REPO}/releases/download/{tag}/{asset_name}"
    if use_proxy:
        return f"{GH_PROXY}/{raw}"
    return raw


# ─── manual package (select local zip) ─────────────────────


# 与 release-package.yml 的发布包命名一致：faust-<tag>-without-runtime.zip
MANUAL_PACKAGE_RE = re.compile(
    r"^faust[-_]?([Vv]\d+\.\d+\.\d+.*?)-without-runtime\.zip$",
    re.IGNORECASE,
)


def validate_manual_package(file_path: str | Path) -> dict[str, str]:
    """校验并暂存手动选择的更新包，返回 {tag, version, asset_name}。

    校验规则：
      1. 文件存在且是 .zip
      2. 文件名匹配发布包命名（含 without-runtime，可解析出版本号）
      3. zip 结构完整（zipfile.is_zipfile）
      4. 版本号高于当前本地版本
    通过后复制到 tempdir/faust_update_{tag}/ 下，供现有更新链路复用。
    """
    src = Path(file_path).expanduser()
    if not src.is_file():
        raise ValueError("文件不存在，请重新选择")

    name = src.name
    m = MANUAL_PACKAGE_RE.match(name)
    if not m:
        raise ValueError(
            f"文件名不符合更新包规范（应为 faust-Vx.y.z-without-runtime.zip）：{name}"
        )
    tag = m.group(1)

    if not zipfile.is_zipfile(src):
        raise ValueError("更新包已损坏（不是有效的 zip 文件），请重新下载")

    current = _current_tag(Path(__file__).resolve().parents[2])
    if not is_newer_version(tag, current):
        raise ValueError(f"更新包版本 {tag} 不高于当前版本 {current}，无需更新")

    # 复制到更新链路约定的位置：tempdir/faust_update_{tag}/{asset_name}
    tmp = Path(tempfile.gettempdir()) / f"faust_update_{tag}"
    tmp.mkdir(parents=True, exist_ok=True)
    dest = tmp / name
    shutil.copy2(src, dest)

    # 若存在旧解压结果，删除强制重新解压，确保用的是刚复制的包
    extracted = tmp / "extracted"
    if extracted.exists():
        shutil.rmtree(extracted)

    log.info(f"Manual update package staged: {name} -> {dest}")
    return {"tag": tag, "version": _normalize_tag(tag), "asset_name": name}


# ─── download progress store ────────────────────────────────


_download_tasks: dict[str, DownloadTask] = {}
_download_tasks_lock = threading.Lock()


def create_download_task(tag: str, asset_name: str, use_proxy: bool) -> str:
    log.info(
        f"Creating download task for tag={tag}, asset={asset_name}, use_proxy={use_proxy}"
    )
    import uuid

    task_id = uuid.uuid4().hex[:12]
    url = _gh_download_url(tag, asset_name, use_proxy)
    tmp = Path(tempfile.gettempdir()) / f"faust_update_{tag}"
    tmp.mkdir(parents=True, exist_ok=True)
    zip_path = tmp / asset_name
    task = DownloadTask(url, zip_path, tag, asset_name)
    with _download_tasks_lock:
        _download_tasks[task_id] = task
    t = threading.Thread(target=task.run, daemon=True)
    t.start()
    return task_id


def get_download_task(task_id: str) -> DownloadTask | None:
    with _download_tasks_lock:
        return _download_tasks.get(task_id)


def cleanup_download_task(task_id: str) -> None:
    with _download_tasks_lock:
        _download_tasks.pop(task_id, None)


# ─── main update manager ────────────────────────────────────


class UpdateManager:
    def __init__(self, project_root: str | Path | None = None):
        self.root = (
            Path(project_root).resolve()
            if project_root
            else Path(__file__).resolve().parents[2]
        )
        self._latest_release: dict[str, Any] | None = None
        self._cached_ts: float = 0.0
        self._tmp_extracted: Path | None = None

    def current_tag(self) -> str:
        return _current_tag(self.root)

    def current_version(self) -> str:
        return _normalize_tag(self.current_tag())

    async def check_latest(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if (
            not force
            and self._latest_release
            and (now - self._cached_ts) < RELEASE_CACHE_HOURS * 3600
        ):
            return self._latest_release

        url = _gh_api_url("releases/latest")
        headers = {"Accept": "application/vnd.github+json"}
        try:
            resp = await asyncio.to_thread(
                requests.get, url, headers=headers, timeout=15
            )
            resp.raise_for_status()
            data: dict = resp.json()
        except Exception as e:
            return {"error": f"检查更新失败: {e}", "available": False}

        tag = str(data.get("tag_name", "")).strip()
        asset_name = self._find_windows_asset(data)
        self._latest_release = {
            "tag": tag,
            "version": _normalize_tag(tag),
            "asset_name": asset_name,
            "published_at": str(data.get("published_at", "")),
            "body": str(data.get("body", "")),
            "available": True,
            "current_tag": self.current_tag(),
            "current_version": self.current_version(),
            "has_update": bool(asset_name) and self._is_newer(tag, self.current_tag()),
        }
        self._cached_ts = now
        return self._latest_release

    def _find_windows_asset(self, release: dict) -> str:
        for asset in release.get("assets") or []:
            name = str(asset.get("name", ""))
            if "without-runtime" in name.lower() and name.endswith(".zip"):
                return name
        return ""

    @staticmethod
    def _is_newer(new_tag: str, old_tag: str) -> bool:
        return is_newer_version(new_tag, old_tag)

    async def ensure_extracted(self, tag: str, asset_name: str) -> Path:
        tmp = Path(tempfile.gettempdir()) / f"faust_update_{tag}"
        zip_path = tmp / asset_name
        if not zip_path.exists():
            raise FileNotFoundError(f"发布包未下载: {zip_path}")
        extract_to = tmp / "extracted"
        if extract_to.exists():
            return self._resolve_extracted_root(extract_to)
        extract_to.mkdir(parents=True, exist_ok=True)

        def _extract():
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_to)

        await asyncio.get_running_loop().run_in_executor(None, _extract)
        return self._resolve_extracted_root(extract_to)

    def _resolve_extracted_root(self, extract_to: Path) -> Path:
        children = list(extract_to.iterdir())
        if len(children) == 1 and children[0].is_dir():
            return children[0]
        return extract_to

    async def diff_release(self, tag: str, asset_name: str) -> dict[str, Any]:
        info = {
            "tag": tag,
            "asset_name": asset_name,
            "preserved": [],
            "overwritten": [],
            "new_files": [],
        }
        src = await self.ensure_extracted(tag, asset_name)
        self._tmp_extracted = src

        src_set: set[str] = set()
        for p in src.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(src).as_posix()
            src_set.add(rel)

        dst_set: set[str] = set()
        for p in self.root.rglob("*"):
            if not p.is_file():
                continue
            rel = p.relative_to(self.root).as_posix()
            if _should_preserve(rel):
                info["preserved"].append(rel)
                continue
            dst_set.add(rel)

        info["overwritten"] = sorted(src_set & dst_set)
        info["new_files"] = sorted(src_set - dst_set)
        return info

    def _preserve_ps1_conditions(self, indent: int = 4) -> list[str]:
        pad = " " * indent
        lines = []
        for pat in PRESERVE_PATTERNS:
            escaped = pat.replace("/", "\\")
            trimmed = escaped.rstrip("\\")
            if pat.endswith("/"):
                lines.append(
                    f"{pad}if ($_.PSIsContainer -and $_.Name -eq '{trimmed}') {{ $skip = $true }}"
                )
            elif pat.startswith("*."):
                ext = pat.lstrip("*")
                lines.append(f"{pad}if ($name -like '*{ext}') {{ $skip = $true }}")
            else:
                lines.append(f"{pad}if ($name -eq '{escaped}') {{ $skip = $true }}")
        return lines

    def _preserve_rel_ps1_conditions(self, indent: int = 4) -> list[str]:
        pad = " " * indent
        lines = []
        for pat in PRESERVE_PATTERNS:
            norm = pat.replace("\\", "/")
            escaped = pat.replace("/", "\\")
            trimmed = escaped.rstrip("\\")
            if pat.endswith("/"):
                lines.append(f"{pad}if ($rel -like '{norm}*') {{ $skip = $true }}")
            elif pat.startswith("*."):
                ext = pat.lstrip("*")
                lines.append(f"{pad}if ($rel -like '*{ext}') {{ $skip = $true }}")
            else:
                lines.append(f"{pad}if ($rel -eq '{escaped}') {{ $skip = $true }}")
        return lines

    def _preserve_dir_names(self) -> list[str]:
        names: list[str] = []
        for pat in PRESERVE_PATTERNS:
            if not pat.endswith("/"):
                continue
            trimmed = pat.rstrip("/")
            if "/" in trimmed:
                continue
            if trimmed:
                names.append(trimmed)
        return names

    def _robocopy_excluded_dirs(self, extracted_src: Path) -> list[str]:
        items: list[str] = []
        for pat in PRESERVE_PATTERNS:
            if not pat.endswith("/"):
                continue
            rel = pat.rstrip("/").replace("/", "\\")
            if not rel:
                continue
            items.append(str((extracted_src / rel).resolve()))
            items.append(str((self.root / rel).resolve()))
        return items

    def _robocopy_excluded_files(self) -> list[str]:
        items: list[str] = []
        for pat in PRESERVE_PATTERNS:
            if pat.startswith("*."):
                items.append(pat)
        return items

    def generate_update_script(
        self, extracted_src: Path, tag: str, dry_run: bool = False
    ) -> str:
        bat_path = Path(tempfile.gettempdir()) / f"faust_apply_update_{tag}.bat"
        excluded_dirs = self._robocopy_excluded_dirs(extracted_src)
        excluded_files = self._robocopy_excluded_files()

        lines = [
            "@echo off",
            "setlocal EnableExtensions",
            "title FaustBot Auto Update",
            f'set "EXTRACTED={extracted_src}"',
            f'set "INSTALL_ROOT={self.root}"',
            f'set "TAG={tag}"',
            f'set "DRY_RUN={1 if dry_run else 0}"',
            "",
            "echo FaustBot update script generated for %TAG%",
            'if not exist "%EXTRACTED%" (',
            "  echo [ERROR] Extracted source not found: %EXTRACTED%",
            "  exit /b 1",
            ")",
            'if not exist "%INSTALL_ROOT%" (',
            "  echo [ERROR] Install root not found: %INSTALL_ROOT%",
            "  exit /b 1",
            ")",
            "",
        ]

        robocopy_cmd = [
            'robocopy "%EXTRACTED%" "%INSTALL_ROOT%" /MIR /R:2 /W:1 /NFL /NDL /NP /MT:16'
        ]
        if dry_run:
            robocopy_cmd.append("/L")
        if excluded_dirs:
            robocopy_cmd.append("/XD")
            robocopy_cmd.extend(f'"{item}"' for item in excluded_dirs)
        if excluded_files:
            robocopy_cmd.append("/XF")
            robocopy_cmd.extend(f'"{item}"' for item in excluded_files)

        lines += [
            "echo Step 0: Kill FaustBot Process if running...",
            "taskkill /F /IM FaustLive2DFrontend.exe >nul 2>&1",#不能使用/T参数
            "taskkill /F /IM python.exe >nul 2>&1",
            "taskkill /F /IM node.exe >nul 2>&1",#mc-operator和MCP
        ]#FIXME: 其实直接杀掉所有python并不是一个好的做法

        lines += [
            "echo Step 1: Syncing update files with robocopy...",
            "set RC_EXIT=0",
            " ".join(robocopy_cmd),
            "set RC_EXIT=%ERRORLEVEL%",
            "if %RC_EXIT% GEQ 8 (",
            "  echo [ERROR] robocopy failed with exit code %RC_EXIT%",
            "  exit /b %RC_EXIT%",
            ")",
            "echo robocopy finished with exit code %RC_EXIT%.",
            "",
        ]


        if not dry_run:
            lines += [
                "echo Step 2: Running setup-runtime.bat to fix Python requirements...",
                'set "SETUP_SCRIPT=%INSTALL_ROOT%\\setup-runtime.bat"',
                'if exist "%SETUP_SCRIPT%" (',
                '  call "%SETUP_SCRIPT%" --fix-py-requirements --source cn --skip-admin-check',
                ") else (",
                "  echo [WARN] setup-runtime.bat not found, skipped.",
                ")",
                "echo Update to %TAG% complete.",
            ]
        else:
            lines += [
                "echo [DRY-RUN] No files were modified.",
            ]

        lines += [
            "echo Congratulations! FaustBot has successfully updated to %TAG%.",
        ]
        
        lines+=["pause"]

        

        log.debug(f"Generated update script for tag={tag}:\n" + "\n".join(lines))
        bat_path.write_text(
            "\r\n".join(lines) + "\r\n", encoding="ascii", errors="ignore"
        )
        return str(bat_path)

    async def apply_update(self, tag: str, asset_name: str) -> dict[str, Any]:
        src = await self.ensure_extracted(tag, asset_name)
        script_path = self.generate_update_script(src, tag)

        CREATE_NEW_CONSOLE = 0x00000010
        proc = subprocess.Popen(
            ["cmd.exe", "/c", script_path],
            creationflags=CREATE_NEW_CONSOLE,
        )

        return {
            "status": "update_prepared",
            "tag": tag,
            "script_path": script_path,
            "pid": proc.pid,
            "message": "更新已准备，应用将在 Faust 退出后开始",
        }

    def cleanup_temp(self) -> None:
        for d in Path(tempfile.gettempdir()).glob("faust_update_*"):
            try:
                if d.is_dir():
                    shutil.rmtree(d)
                else:
                    d.unlink()
            except Exception:
                pass

