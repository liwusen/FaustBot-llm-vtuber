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
log=logger.get_logger("faust.update.manager")

GITHUB_OWNER = "liwusen"
GITHUB_REPO = "FaustBot-llm-vtuber"
GH_PROXY = "https://edgeone.gh-proxy.org"
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
]


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


# ─── threaded download task ─────────────────────────────────


class DownloadTask:
    def __init__(self, url: str, dest_path: Path, tag: str, asset_name: str,
                 num_threads: int = DOWNLOAD_THREADS):
        self.url = url
        self.dest_path = dest_path
        self.tag = tag
        self.asset_name = asset_name
        self.num_threads = num_threads
        self.total_bytes: int = 0
        self.downloaded_bytes: int = 0
        self._lock = threading.Lock()
        self._start_time: float = 0.0
        self._done = False
        self._error: str | None = None
        self._progress_cb: Callable | None = None

    @property
    def elapsed(self) -> float:
        return time.time() - self._start_time if self._start_time else 0.0

    @property
    def speed_mbps(self) -> float:
        e = self.elapsed
        return (self.downloaded_bytes / 1_048_576) / e if e > 0 else 0.0

    @property
    def progress(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return min(1.0, self.downloaded_bytes / self.total_bytes)

    @property
    def done(self) -> bool:
        return self._done

    @property
    def error(self) -> str | None:
        return self._error

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "asset_name": self.asset_name,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "total_mb": round(self.total_bytes / 1_048_576, 1) if self.total_bytes else 0,
            "downloaded_mb": round(self.downloaded_bytes / 1_048_576, 1),
            "progress": round(self.progress * 100, 1),
            "speed_mbps": round(self.speed_mbps, 1),
            "elapsed_sec": round(self.elapsed, 1),
            "done": self._done,
            "error": self._error,
        }

    def _request_with_retry(self, method: str, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                r = requests.request(method, self.url, **kwargs)
                r.raise_for_status()
                return r
            except Exception as e:
                last_exc = e
                if attempt < DOWNLOAD_MAX_RETRIES:
                    delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.warning(f"Download attempt {attempt}/{DOWNLOAD_MAX_RETRIES} failed for "
                                f"{self.asset_name}, retrying in {delay:.1f}s: {e}")
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _dl_chunk_with_retry(self, start: int, end: int, idx: int) -> Path:
        part = self.dest_path.with_suffix(f".part{idx}")
        h = {"Range": f"bytes={start}-{end}"}
        last_exc: Exception | None = None
        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                r = requests.get(self.url, headers=h, stream=True, timeout=120)
                r.raise_for_status()
                with open(part, "wb") as f:
                    for data in r.iter_content(DOWNLOAD_CHUNK_SIZE):
                        if data:
                            f.write(data)
                            with self._lock:
                                self.downloaded_bytes += len(data)
                return part
            except Exception as e:
                last_exc = e
                if attempt < DOWNLOAD_MAX_RETRIES:
                    delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.warning(f"Chunk {idx} attempt {attempt}/{DOWNLOAD_MAX_RETRIES} failed "
                                f"({start}-{end}), retrying in {delay:.1f}s: {e}")
                    time.sleep(delay)
                if part.exists():
                    part.unlink()
        raise last_exc  # type: ignore[misc]

    def run(self) -> None:
        self._start_time = time.time()
        try:
            head = self._request_with_retry("HEAD", timeout=15)
            self.total_bytes = int(head.headers.get("content-length", 0))
            if self.total_bytes == 0:
                raise RuntimeError("无法获取文件大小")

            self.dest_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.dest_path, "wb") as f:
                f.truncate(self.total_bytes)

            chunk = self.total_bytes // self.num_threads
            ranges: list[tuple[int, int, int]] = []
            for i in range(self.num_threads):
                start = i * chunk
                end = start + chunk - 1 if i < self.num_threads - 1 else self.total_bytes - 1
                if start <= end:
                    ranges.append((start, end, i))

            part_paths: list[Path] = []

            with concurrent.futures.ThreadPoolExecutor(max_workers=self.num_threads) as pool:
                fut_map = {pool.submit(self._dl_chunk_with_retry, s, e, i): i for s, e, i in ranges}
                for fut in concurrent.futures.as_completed(fut_map):
                    exc = fut.exception()
                    if exc:
                        raise exc
                    part_paths.append(fut.result())

            part_paths.sort(key=lambda p: int(p.suffix.replace(".part", "")))
            with open(self.dest_path, "wb") as out:
                for pp in part_paths:
                    with open(pp, "rb") as f:
                        shutil.copyfileobj(f, out)
                    pp.unlink()

            self._done = True
        except Exception as e:
            self._error = str(e)


# ─── download progress store ────────────────────────────────


_download_tasks: dict[str, DownloadTask] = {}
_download_tasks_lock = threading.Lock()


def create_download_task(tag: str, asset_name: str, use_proxy: bool) -> str:
    log.info(f"Creating download task for tag={tag}, asset={asset_name}, use_proxy={use_proxy}")
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
        self.root = Path(project_root).resolve() if project_root else Path(__file__).resolve().parents[2]
        self._latest_release: dict[str, Any] | None = None
        self._cached_ts: float = 0.0
        self._tmp_extracted: Path | None = None

    def current_tag(self) -> str:
        return _current_tag(self.root)

    def current_version(self) -> str:
        return _normalize_tag(self.current_tag())

    async def check_latest(self, *, force: bool = False) -> dict[str, Any]:
        now = time.time()
        if not force and self._latest_release and (now - self._cached_ts) < RELEASE_CACHE_HOURS * 3600:
            return self._latest_release

        url = _gh_api_url("releases/latest")
        headers = {"Accept": "application/vnd.github+json"}
        try:
            resp = await asyncio.to_thread(requests.get, url, headers=headers, timeout=15)
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
        def _parts(t: str) -> tuple:
            cleaned = t.lstrip("Vv")
            parts = []
            for seg in re.split(r"[._\-]", cleaned):
                try:
                    parts.append(int(seg))
                except ValueError:
                    parts.append(seg)
            return tuple(parts)

        return _parts(new_tag) > _parts(old_tag)

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
        info = {"tag": tag, "asset_name": asset_name, "preserved": [], "overwritten": [], "new_files": []}
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
                lines.append(f"{pad}if ($_.PSIsContainer -and $_.Name -eq '{trimmed}') {{ $skip = $true }}")
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

    def generate_update_script(self, extracted_src: Path, tag: str, dry_run: bool = False) -> str:
        bat_path = Path(tempfile.gettempdir()) / f"faust_apply_update_{tag}.ps1"

        preserve_conds = self._preserve_ps1_conditions(8)
        preserve_rel_conds = self._preserve_rel_ps1_conditions(8)
        preserve_dir_names = self._preserve_dir_names()

        lines = [
            "#!powershell",
            "# Faust Auto-Update Script  (generated)",
            "param([switch]$DryRun)",
            "",
            f'$extracted = "{extracted_src}"',
            f'$installRoot = "{self.root}"',
            f'$tag = "{tag}"',
            "",
            'if ($DryRun) {',
            "    Write-Host '[DRY-RUN] Mode enabled - no files will be modified'",
            "}",
            '$copyCount = 0',
            '$removeCount = 0',
            "",
        ]

        if not dry_run:
            lines += [
                "# Wait for Faust to exit",
                "Write-Host 'Waiting for Faust processes to exit...'",
                "do {",
                "    $procs = Get-Process -Name 'electron','python','faust*' -ErrorAction SilentlyContinue",
                "    if (-not $procs) { break }",
                "    Write-Host '  Waiting...'",
                "    Start-Sleep -Seconds 2",
                "} while ($true)",
                "Write-Host 'All FaustBot processes exited.'",
                "",
            ]

        # ── Step 1: Copy new files ──
        lines += [
            "# ── Step 1: Copy new files (skip preserved) ──",
            'if ($DryRun) { Write-Host "Step 1: Scanning files to copy..." }',
            'else { Write-Host "Step 1: Copying update files..." }',
            "function Copy-FaustTree($srcRoot, $dstRoot, $prefix) {",
            "    Get-ChildItem -LiteralPath $srcRoot -Force | ForEach-Object {",
            "        $name = $_.Name",
            '        $rel = if ([string]::IsNullOrEmpty($prefix)) { $name } else { "$prefix/$name" }',
            "        $skip = $false",
        ]
        lines += preserve_rel_conds
        lines += [
            "        if ($skip) { return }",
            "        $dest = Join-Path $dstRoot $name",
            '        if ($_.PSIsContainer) {',
            '            if (-not $DryRun -and -not (Test-Path $dest)) { New-Item -ItemType Directory -Path $dest -Force | Out-Null }',
            '            Copy-FaustTree $_.FullName $dest $rel',
            '            return',
            '        }',
            '        if ($DryRun) {',
            '            Write-Host "  [DRY-RUN] Would copy: $name"',
            '            return',
            '        }',
            '        Copy-Item -LiteralPath $_.FullName -Destination $dest -Force',
            '        $script:copyCount += 1',
            '        if (($script:copyCount % 100) -eq 0) { Write-Host ("{0} file writed" -f $script:copyCount) }',
            "    }",
            "}",
            "Copy-FaustTree $extracted $installRoot ''",
            'if (-not $DryRun) { Write-Host "Step 1 done." }',
            "",
        ]

        # ── Step 2: Remove orphaned files ──
        lines += [
            "# ── Step 2: Remove files and folders that exist locally but not in new release ──",
            'if ($DryRun) { Write-Host "Step 2: Scanning for orphaned items to remove..." }',
            'else { Write-Host "Step 2: Removing orphaned items..." }',
            "",
            "# Build relative path set from extracted release",
            '$newItems = @{}',
            "function Collect-NewItems($srcRoot, $prefix) {",
            '    Get-ChildItem -LiteralPath $srcRoot -Force | ForEach-Object {',
            '        $name = $_.Name',
            '        $rel = if ([string]::IsNullOrEmpty($prefix)) { $name } else { "$prefix/$name" }',
            '        $skip = $false',
        ]
        lines += preserve_rel_conds
        lines += [
            '        if ($skip) { return }',
            '        $script:newItems[$rel.ToLower()] = $true',
            '        if ($_.PSIsContainer) { Collect-NewItems $_.FullName $rel }',
            '    }',
            '}',
            'Collect-NewItems $extracted ""',
            "",
            "# Walk install root and delete items not in new release (skip preserved)",
            '$preserveDirNames = @(' + ', '.join(f'"{name}"' for name in preserve_dir_names) + ')',
            'function Remove-Orphans($root, $prefix) {',
            '    Get-ChildItem -LiteralPath $root -Force | ForEach-Object {',
            '        $name = $_.Name',
            '        if ($_.PSIsContainer -and ($preserveDirNames -contains $name)) { return }',
            '        $rel = if ([string]::IsNullOrEmpty($prefix)) { $name } else { "$prefix/$name" }',
            '        if ($script:newItems.ContainsKey($rel.ToLower())) {',
            '            if ($_.PSIsContainer) { Remove-Orphans $_.FullName $rel }',
            '            return',
            '        }',
            '        $skip = $false',
        ]
        lines += preserve_rel_conds
        lines += [
            '        if ($skip) { return }',
            '        if ($DryRun) {',
            '            Write-Host "  [DRY-RUN] Would remove: $rel"',
            '            return',
            '        }',
            '        Remove-Item -LiteralPath $_.FullName -Recurse -Force -ErrorAction SilentlyContinue',
            '        $script:removeCount += 1',
            '        if (($script:removeCount % 100) -eq 0) { Write-Host ("{0} file writed" -f $script:removeCount) }',
            '    }',
            '}',
            'Remove-Orphans $installRoot ""',
            "",
            "# Remove empty directories left behind",
            'if (-not $DryRun) {',
            '    $dirs = Get-ChildItem -LiteralPath $installRoot -Recurse -Directory -Force | Sort-Object FullName -Descending',
            '    foreach ($d in $dirs) {',
            '        $hasChildren = @(Get-ChildItem -LiteralPath $d.FullName -Force -ErrorAction SilentlyContinue).Count -gt 0',
            '        if (-not $hasChildren) {',
            '            Remove-Item -LiteralPath $d.FullName -Force -ErrorAction SilentlyContinue',
            '        }',
            '    }',
            '}',
            'if (-not $DryRun) { Write-Host "Step 2 done." }',
            "",
        ]

        # ── Finalize ──
        if not dry_run:
            lines += [
                "# ── Step 3: Run setup ──",
                'Write-Host "Step 3: Running setup-runtime.bat --torch cpu --tts no..."',
                '$setup = Join-Path $installRoot "setup-runtime.bat"',
                'if (Test-Path $setup) {',
                '    & $setup --torch cpu --tts no --install-python no --install-node yes --source cn',
                '}',
                'Write-Host "Setup complete."',
                'Write-Host "Update to $tag complete."',
                'Start-Sleep -Seconds 3',
            ]
        else:
            lines += [
                'Write-Host "[DRY-RUN] No files were modified."',
            ]
        log.debug(f"Generated update script for tag={tag}:\n" + "\n".join(lines))
        bat_path.write_text("\n".join(lines), encoding="utf-8")
        return str(bat_path)

    async def apply_update(self, tag: str, asset_name: str) -> dict[str, Any]:
        src = await self.ensure_extracted(tag, asset_name)
        script_path = self.generate_update_script(src, tag)

        CREATE_NEW_CONSOLE = 0x00000010
        proc = subprocess.Popen(
            ["powershell", "-ExecutionPolicy", "Bypass", "-File", script_path],
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
