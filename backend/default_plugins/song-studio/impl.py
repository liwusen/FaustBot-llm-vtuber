"""歌台 Song Studio：Seed-VC + audio-separator 歌声转换与演唱插件。

- 推理在独立 sva-runtime（embeddable Python）子进程中执行
- 主进程只做任务排队、进度转发（SSE）、播放指令下发（bridge SING/SINGSTOP）
"""
from __future__ import annotations

import asyncio
import importlib.util
import json
import os
import queue
import subprocess
import sys
import threading
import time
import uuid
from pathlib import Path
from typing import Any

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from faust_backend.logger import get_logger
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

log = get_logger("faust.plugin.song-studio")

_PLUGIN_DIR = Path(__file__).resolve().parent


def _load_sibling(name: str):
    module_name = f"faust_plugin_song_studio_{name}"
    if module_name in sys.modules:
        return sys.modules[module_name]
    spec = importlib.util.spec_from_file_location(module_name, _PLUGIN_DIR / f"{name}.py")
    if spec is None or spec.loader is None:
        raise ImportError(f"cannot load {name}.py from {_PLUGIN_DIR}")
    module = importlib.util.module_from_spec(spec)
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    return module


library = _load_sibling("library")
runtime_installer = _load_sibling("runtime_installer")

TERMINAL_STATES = {"done", "error", "cancelled"}


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext | None = None
        self.lib: Any = None
        self.runtime_dir: Path | None = None
        self._jobs: dict[str, dict] = {}
        self._jobs_lock = threading.Lock()
        self._job_queue: queue.Queue = queue.Queue()
        self._worker: threading.Thread | None = None
        self._active_proc: subprocess.Popen | None = None
        self._singing: dict | None = None
        self.logs_dir: Path | None = None

    # ── lifecycle ──

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        data_dir = ctx.plugin_data_dir or (ctx.plugin_dir / "data")
        self.lib = library.SongLibrary(data_dir)
        self.runtime_dir = data_dir.parent / "sva-runtime"
        self.logs_dir = data_dir / "logs"
        self.logs_dir.mkdir(parents=True, exist_ok=True)
        await ctx.register_config(
            [
                {"key": "REF_AUDIO_PATH", "type": "str", "label": "参考音色音频路径（留空使用 TTS 参考音频）", "default": ""},
                {"key": "DIFFUSION_STEPS", "type": "int", "label": "Seed-VC 扩散步数", "default": 50},
                {"key": "SEMI_TONE_SHIFT", "type": "int", "label": "整体移调（半音）", "default": 0},
                {"key": "AUTO_F0_ADJUST", "type": "bool", "label": "自动音高对齐", "default": True},
                {"key": "VOCAL_GAIN_DB", "type": "float", "label": "人声增益（dB）", "default": 0.0},
                {"key": "USE_PYPI_MIRROR", "type": "bool", "label": "安装依赖时使用阿里云 PyPI 镜像", "default": True},
            ]
        )
        self._configs_cache = await ctx.list_configs()
        self._worker = threading.Thread(target=self._worker_loop, daemon=True, name="song-studio-worker")
        self._worker.start()
        self._write_vfs_index()

    @hookimpl
    async def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        # 用 startup 保存的 self.ctx（hook 参数 ctx 可能为 None）
        if self.ctx is not None:
            self._configs_cache = await self.ctx.list_configs()

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        log.info("Song Studio plugin loaded")

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        self._stop_singing(notify_frontend=False)
        proc = self._active_proc
        if proc is not None and proc.poll() is None:
            proc.kill()

    def register_frontend(self) -> list[dict]:
        return [
            {"type": "js", "path": "/faust/plugins/song-studio/frontend/panel-v2.js"},
            {"type": "js", "path": "/faust/plugins/song-studio/frontend/app-hook-v2.js"},
            {"type": "css", "path": "/faust/plugins/song-studio/frontend/panel-v2.css"},
        ]

    def register_prompt_suffix(self) -> list[str]:
        return [
            "\n[歌台 Song Studio]\n"
            "你拥有唱歌能力（AI 歌声转换，会用你自己的音色演唱曲库中的歌曲）。\n"
            "- 曲库与转换状态：读取 faustbot://plugins/song-studio/songs.md\n"
            "- 用户点歌时调用 singSong(name) 开始演唱；歌曲未转换时会自动排队转换（首次需数分钟），转换完成后自动开始演唱。\n"
            "- 用户要求停止时调用 stopSinging()。\n"
            "- 演唱期间你仍可正常对话，但注意言简意赅，不要打断歌曲太久。\n"
        ]

    def health_check(self) -> dict | None:
        return {
            "status": "ok",
            "plugin": "song-studio",
            "runtime_installed": self._runtime_installed(),
            "singing": self._singing is not None,
        }

    def heartbeat(self, ctx: PluginContext) -> None:
        # 演唱期间持续压制触发器（chat 回合结束会 clear 该事件）
        if self._singing is not None:
            try:
                from faust_backend.events import get_bus
                get_bus().ignore_trigger_event.set()
            except Exception:
                pass

    # ── helpers ──

    def _get_config_sync(self, key: str, default: Any = None) -> Any:
        cache = getattr(self, "_configs_cache", {})
        return cache.get(key, default)

    def _runtime_installed(self) -> bool:
        return self.runtime_dir is not None and runtime_installer.is_installed(self.runtime_dir)

    def _installed_torch_variant(self) -> str | None:
        if self.runtime_dir is None:
            return None
        marker = self.runtime_dir / "torch-variant.txt"
        try:
            return marker.read_text(encoding="utf-8").strip() or None
        except OSError:
            return None

    def _ref_file(self) -> Path:
        assert self.ctx is not None
        raw = str(self._get_config_sync("REF_AUDIO_PATH", "") or "").strip()
        if raw:
            path = Path(raw).expanduser()
            if not path.exists():
                raise RuntimeError(f"参考音色音频不存在: {path}")
            return path
        import faust_backend.config_loader as conf
        refer = str(getattr(conf, "TTS_REFER_WAV_PATH", "") or "").strip()
        if not refer:
            raise RuntimeError("未配置参考音色：请在插件配置中设置 REF_AUDIO_PATH，或配置 TTS 参考音频")
        if refer=="voices/neuro.wav":
            refer = "voices/neuro_long.wav"
        path = Path(refer).expanduser()
        if not path.is_absolute():
            path = Path(conf.CONFIG_ROOT) / path
        if not path.exists():
            raise RuntimeError(f"TTS 参考音频不存在: {path}")
        return path

    def _convert_params(self) -> dict:
        assert self.ctx is not None
        return {
            "diffusion_steps": int(self._get_config_sync("DIFFUSION_STEPS", 50) or 50),
            "semi_tone_shift": int(self._get_config_sync("SEMI_TONE_SHIFT", 0) or 0),
            "auto_f0": bool(self._get_config_sync("AUTO_F0_ADJUST", True)),
            "vocal_gain_db": float(self._get_config_sync("VOCAL_GAIN_DB", 0.0) or 0.0),
        }

    def _song_status(self, song: dict, ref_file: Path | None, params: dict) -> dict:
        info = dict(song)
        info["cached"] = False
        info["cache_key"] = None
        if ref_file is not None:
            try:
                entry = self.lib.cache_entry(Path(song["file"]), ref_file, params)
                info["cached"] = bool(entry["ready"])
                info["cache_key"] = entry["key"]
            except OSError as exc:
                info["error"] = str(exc)
        return info

    def _list_songs_with_status(self) -> list[dict]:
        params = self._convert_params()
        try:
            ref_file = self._ref_file()
        except RuntimeError:
            ref_file = None
        return [self._song_status(song, ref_file, params) for song in self.lib.list_source_songs()]

    def _write_vfs_index(self) -> None:
        if self.ctx is None:
            return

        async def run_async():
            try:
                songs = self._list_songs_with_status()
            except Exception as exc:
                log.warning("song-studio VFS index failed: %s", exc)
                return
            lines = [
                "# 歌台 Song Studio 曲库",
                "",
                f"独立推理环境: {'已安装' if self._runtime_installed() else '未安装（需在插件面板中一键安装）'}",
                "",
                "| 歌曲 | 已转换 | 歌词 |",
                "| --- | --- | --- |",
            ]
            if not songs:
                lines.append("| (曲库为空，请将歌曲文件放入 library/source 目录) | - | - |")
            for song in songs:
                lines.append(
                    f"| {song['name']} | {'是' if song.get('cached') else '否'} | {'有' if song.get('lrc') else '无'} |"
                )
            lines += [
                "",
                f"曲库目录: {self.lib.source_dir}",
                "用 singSong(name) 演唱，未转换的歌曲会自动排队转换后开唱。",
            ]
            try:
                await self.ctx.vfs_write("/plugins/song-studio/songs.md", "\n".join(lines) + "\n")
            except Exception as exc:
                log.warning("song-studio vfs_write failed: %s", exc)

        try:
            loop = asyncio.get_running_loop()
            if loop.is_running():
                asyncio.run_coroutine_threadsafe(run_async(), loop)
            else:
                loop.run_until_complete(run_async())
        except RuntimeError:
            try:
                loop = asyncio.get_event_loop_policy().get_event_loop()
                if loop.is_running():
                    asyncio.run_coroutine_threadsafe(run_async(), loop)
                else:
                    loop.run_until_complete(run_async())
            except Exception:
                asyncio.run(run_async())

    # ── job queue ──

    def _new_job(self, job_type: str, detail: dict | None = None) -> dict:
        job = {
            "id": uuid.uuid4().hex[:12],
            "type": job_type,
            "status": "queued",
            "stage": "queued",
            "percent": 0.0,
            "message": "排队中...",
            "log": [],
            "error": None,
            "created_at": time.time(),
            "cancel": False,
            **(detail or {}),
        }
        with self._jobs_lock:
            self._jobs[job["id"]] = job
        self._job_queue.put(job["id"])
        return job

    def _job_snapshot(self, job_id: str) -> dict | None:
        with self._jobs_lock:
            job = self._jobs.get(job_id)
            if job is None:
                return None
            snap = {k: v for k, v in job.items() if k not in ("cancel", "log")}
            snap["log_tail"] = list(job["log"][-8:])
            return snap

    def _update_job(self, job: dict, *, stage: str | None = None, percent: float | None = None,
                    message: str | None = None, status: str | None = None, error: str | None = None) -> None:
        with self._jobs_lock:
            if stage is not None:
                job["stage"] = stage
            if percent is not None and percent >= 0:
                job["percent"] = round(float(percent), 1)
            if message is not None:
                job["message"] = message
                job["log"].append(message)
                job["log"] = job["log"][-100:]
            if status is not None:
                job["status"] = status
            if error is not None:
                job["error"] = error

    def _worker_loop(self) -> None:
        while True:
            job_id = self._job_queue.get()
            with self._jobs_lock:
                job = self._jobs.get(job_id)
            if job is None:
                continue
            if job["cancel"]:
                self._update_job(job, status="cancelled", message="已取消")
                continue
            self._update_job(job, status="running", stage="start", message="任务开始")
            try:
                if job["type"] == "install":
                    self._run_install(job)
                elif job["type"] == "convert":
                    self._run_convert(job)
                else:
                    raise RuntimeError(f"未知任务类型: {job['type']}")
                if job["cancel"]:
                    self._update_job(job, status="cancelled", message="已取消")
                else:
                    self._update_job(job, status="done", stage="done", percent=100.0)
            except Exception as exc:
                log.error("song-studio job %s failed: %s", job_id, exc)
                self._update_job(job, status="error", error=str(exc), message=str(exc))
            finally:
                self._write_vfs_index()

    def _run_install(self, job: dict) -> None:
        assert self.ctx is not None and self.runtime_dir is not None

        def progress(stage: str, percent: float, message: str) -> None:
            self._update_job(job, stage=stage, percent=percent, message=message)

        runtime_installer.install_runtime(
            self.runtime_dir, progress, cancel_check=lambda: bool(job["cancel"]),
            use_pypi_mirror=bool(self._get_config_sync("USE_PYPI_MIRROR", True)),
            local_seedvc_zip=self.lib.cache_dir / runtime_installer.SEEDVC_ZIP_NAME,
            torch_variant=str(job.get("torch_variant") or runtime_installer.DEFAULT_TORCH_VARIANT))

    def _run_convert(self, job: dict) -> None:
        assert self.runtime_dir is not None
        if not self._runtime_installed():
            raise RuntimeError("sva-runtime 未安装，请先在插件面板中安装独立推理环境")
        song = job["song"]
        ref_file = Path(job["ref_file"])
        params = job["params"]
        entry = self.lib.cache_entry(Path(song["file"]), ref_file, params)
        job["cache_key"] = entry["key"]
        if entry["ready"]:
            self._update_job(job, stage="done", percent=100.0, message="命中缓存，无需转换")
            if job.get("auto_play"):
                self._start_singing(song, entry)
            return

        started = time.time()
        entry["dir"].mkdir(parents=True, exist_ok=True)
        cmd = [
            str(runtime_installer.runtime_python(self.runtime_dir)),
            str(_PLUGIN_DIR / "worker" / "convert_song.py"),
            "--source", song["file"],
            "--reference", str(ref_file),
            "--output", str(entry["final"]),
            "--seedvc-dir", str(runtime_installer.seedvc_dir(self.runtime_dir)),
            "--model-dir", str(self.runtime_dir / "models"),
            "--diffusion-steps", str(params["diffusion_steps"]),
            "--semi-tone-shift", str(params["semi_tone_shift"]),
            "--vocal-gain-db", str(params["vocal_gain_db"]),
        ]
        if params["auto_f0"]:
            cmd.append("--auto-f0")
        log.info("song-studio convert: %s", " ".join(cmd))
        assert self.logs_dir is not None
        log_path = self.logs_dir / f"{time.strftime('%Y%m%d-%H%M%S')}_convert_{job['id']}.log"
        job["log_file"] = log_path.name
        proc = subprocess.Popen(
            cmd, cwd=str(self.runtime_dir),
            stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
            text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONNOUSERSITE": "1", "PYTHONIOENCODING": "utf-8"},
            creationflags=subprocess.CREATE_NEW_CONSOLE if sys.platform == "win32" else 0)
        self._active_proc = proc
        worker_error: str | None = None
        try:
            with open(log_path, "w", encoding="utf-8") as logf:
                logf.write(f"# song: {song['name']}\n# job: {job['id']}\n# cmd: {' '.join(cmd)}\n\n")
                assert proc.stdout is not None
                for line in proc.stdout:
                    logf.write(line)
                    logf.flush()
                    if job["cancel"]:
                        proc.kill()
                        logf.write("\n# cancelled\n")
                        return
                    line = line.strip()
                    if not line:
                        continue
                    try:
                        event = json.loads(line)
                    except ValueError:
                        self._update_job(job, message=line)
                        continue
                    stage = str(event.get("stage") or "")
                    if stage == "error":
                        worker_error = str(event.get("message") or "convert worker error")
                        continue
                    self._update_job(
                        job, stage=stage, percent=float(event.get("percent") or -1),
                        message=str(event.get("message") or ""))
                code = proc.wait()
                logf.write(f"\n# exit code: {code}\n")
        finally:
            self._active_proc = None
        if code != 0:
            raise RuntimeError(worker_error or f"转换 worker 失败 (exit {code})")
        if not entry["final"].exists():
            raise RuntimeError(f"转换完成但缺少输出文件: {entry['final']}")
        self.lib.write_meta(entry, song, ref_file, params, time.time() - started)
        if job.get("auto_play") and not job["cancel"]:
            self._start_singing(song, entry)

    # ── singing control ──

    def _start_singing(self, song: dict, entry: dict) -> None:
        from faust_backend.frontend import get_bridge
        lyrics = None
        lrc_path = song.get("lrc")
        if lrc_path:
            try:
                lyrics = Path(lrc_path).read_text(encoding="utf-8", errors="replace")
            except OSError:
                lyrics = None
        payload = {
            "title": song["name"],
            "url": Path(entry["final"]).resolve().as_uri(),
            "lyrics": lyrics,
        }
        self._singing = {"song": song["name"], "cache_key": entry["key"], "started_at": time.time()}
        try:
            from faust_backend.events import get_bus
            get_bus().ignore_trigger_event.set()
        except Exception:
            pass
        get_bridge().sing(payload)
        log.info("song-studio start singing: %s", song["name"])

    def _stop_singing(self, notify_frontend: bool = True) -> bool:
        was_singing = self._singing is not None
        self._singing = None
        try:
            from faust_backend.events import get_bus
            get_bus().ignore_trigger_event.clear()
        except Exception:
            pass
        if notify_frontend and was_singing:
            from faust_backend.frontend import get_bridge
            get_bridge().sing_stop()
        return was_singing

    def _request_sing(self, name: str) -> str:
        song = self.lib.find_song(name)
        if song is None:
            available = ", ".join(s["name"] for s in self.lib.list_source_songs()) or "(曲库为空)"
            return f"曲库中没有找到《{name}》。可用歌曲: {available}"
        params = self._convert_params()
        ref_file = self._ref_file()
        entry = self.lib.cache_entry(Path(song["file"]), ref_file, params)
        if entry["ready"]:
            self._start_singing(song, entry)
            return f"开始演唱《{song['name']}》。"
        if not self._runtime_installed():
            return "这首歌还没有转换，且独立推理环境（sva-runtime）尚未安装。请让用户在插件面板中先安装推理环境。"
        job = self._new_job("convert", {
            "song": song, "ref_file": str(ref_file), "params": params, "auto_play": True,
        })
        return (
            f"《{song['name']}》尚未转换，已加入转换队列（任务 {job['id']}），"
            "首次转换需要几分钟，完成后会自动开始演唱。"
        )

    # ── tools ──

    def register_tools(self, ctx: PluginContext) -> list:
        plugin = self

        @tool
        def singSong(name: str) -> str:
            """
            Description:
                用自己的歌声演唱曲库中的歌曲（AI 歌声转换）。曲库列表见 faustbot://plugins/song-studio/songs.md。
                如果歌曲尚未转换，会自动排队转换（首次需要几分钟），完成后自动开始演唱。
            Args:
                name (str): 歌曲名称（支持模糊匹配）。
            Returns:
                str: 操作结果。
            """
            try:
                return plugin._request_sing(name)
            except Exception as exc:
                return f"点歌失败: {exc}"

        @tool
        def stopSinging() -> str:
            """
            Description:
                停止当前正在演唱的歌曲。
            Args:
                None
            Returns:
                str: 操作结果。
            """
            try:
                if plugin._stop_singing():
                    return "已停止演唱。"
                return "当前没有正在演唱的歌曲。"
            except Exception as exc:
                return f"停止失败: {exc}"

        return [
            ToolSpec(name="singSong", tool=singSong, enabled_by_default=True,
                     description=singSong.__doc__ or ""),
            ToolSpec(name="stopSinging", tool=stopSinging, enabled_by_default=True,
                     description=stopSinging.__doc__ or ""),
        ]

    # ── frontend communication ──

    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get("action") or "").strip().lower()
        try:
            if action == "status":
                with self._jobs_lock:
                    jobs = sorted(self._jobs.values(), key=lambda j: j["created_at"], reverse=True)
                    job_list = [
                        {k: v for k, v in job.items() if k not in ("cancel", "log", "song")}
                        for job in jobs[:10]
                    ]
                try:
                    ref_audio = str(self._ref_file())
                    ref_error = None
                except RuntimeError as exc:
                    ref_audio = None
                    ref_error = str(exc)
                return {
                    "status": "ok",
                    "runtime_installed": self._runtime_installed(),
                    "runtime_dir": str(self.runtime_dir),
                    "torch_variant": self._installed_torch_variant(),
                    "torch_variants": list(runtime_installer.TORCH_VARIANTS),
                    "default_torch_variant": runtime_installer.DEFAULT_TORCH_VARIANT,
                    "source_dir": str(self.lib.source_dir),
                    "ref_audio": ref_audio,
                    "ref_audio_error": ref_error,
                    "ref_audio_config": str(self._get_config_sync("REF_AUDIO_PATH", "") or ""),
                    "singing": dict(self._singing) if self._singing else None,
                    "jobs": job_list,
                }
            if action == "list_songs":
                return {"status": "ok", "items": self._list_songs_with_status()}
            if action == "install_runtime":
                variant = str((payload or {}).get("torch_variant") or runtime_installer.DEFAULT_TORCH_VARIANT)
                if variant not in runtime_installer.TORCH_VARIANTS:
                    return {"status": "error", "detail": f"未知 torch 变体: {variant}"}
                with self._jobs_lock:
                    busy = any(j["type"] == "install" and j["status"] in ("queued", "running")
                               for j in self._jobs.values())
                if busy:
                    return {"status": "error", "detail": "已有安装任务在进行中"}
                job = self._new_job("install", {"torch_variant": variant})
                return {"status": "ok", "job_id": job["id"]}
            if action == "convert_song":
                name = str((payload or {}).get("name") or "").strip()
                song = self.lib.find_song(name)
                if song is None:
                    return {"status": "error", "detail": f"歌曲不存在: {name}"}
                job = self._new_job("convert", {
                    "song": song,
                    "ref_file": str(self._ref_file()),
                    "params": self._convert_params(),
                    "auto_play": bool((payload or {}).get("auto_play")),
                })
                return {"status": "ok", "job_id": job["id"]}
            if action == "cancel_job":
                job_id = str((payload or {}).get("job_id") or "")
                with self._jobs_lock:
                    job = self._jobs.get(job_id)
                    if job is None:
                        return {"status": "error", "detail": "任务不存在"}
                    job["cancel"] = True
                proc = self._active_proc
                if proc is not None and proc.poll() is None:
                    proc.kill()
                return {"status": "ok"}
            if action == "delete_cache":
                key = str((payload or {}).get("key") or "")
                deleted = self.lib.delete_cache(key)
                self._write_vfs_index()
                return {"status": "ok", "deleted": deleted}
            if action == "sing":
                return {"status": "ok", "detail": self._request_sing(str((payload or {}).get("name") or ""))}
            if action == "stop_sing":
                return {"status": "ok", "was_singing": self._stop_singing()}
            if action == "song_finished":
                self._stop_singing(notify_frontend=False)
                return {"status": "ok"}
            if action == "list_logs":
                assert self.logs_dir is not None
                items = []
                for path in sorted(self.logs_dir.glob("*.log"),
                                   key=lambda p: p.stat().st_mtime, reverse=True):
                    stat = path.stat()
                    items.append({"name": path.name, "size": stat.st_size, "mtime": stat.st_mtime})
                return {"status": "ok", "items": items}
            if action == "get_log":
                assert self.logs_dir is not None
                name = str((payload or {}).get("name") or "")
                # name 来自前端输入，防路径穿越
                if "/" in name or "\\" in name or ".." in name or not name.endswith(".log"):
                    return {"status": "error", "detail": f"非法日志名: {name}"}
                path = self.logs_dir / name
                if not path.exists():
                    return {"status": "error", "detail": f"日志不存在: {name}"}
                content = path.read_text(encoding="utf-8", errors="replace")
                truncated = len(content) > 512 * 1024
                if truncated:
                    content = content[-512 * 1024:]
                return {"status": "ok", "name": name, "content": content, "truncated": truncated}
            if action == "rescan":
                self._write_vfs_index()
                return {"status": "ok", "items": self._list_songs_with_status()}
        except Exception as exc:
            log.error("song-studio communicate %s failed: %s", action, exc)
            return {"status": "error", "detail": str(exc)}
        return {"status": "error", "detail": f"unknown action: {action}"}

    def sse_communicate_handler(self, params: dict, ctx: PluginContext) -> Any:
        job_id = str((params or {}).get("job_id") or "").strip()
        interval = 0.5 if job_id else 1.0

        async def stream():
            while True:
                if job_id:
                    snap = self._job_snapshot(job_id)
                    if snap is None:
                        yield {"kind": "error", "detail": f"任务不存在: {job_id}"}
                        return
                    yield {"kind": "job", **snap}
                    if snap["status"] in TERMINAL_STATES:
                        return
                else:
                    with self._jobs_lock:
                        active = [
                            {k: v for k, v in job.items() if k not in ("cancel", "log", "song")}
                            for job in self._jobs.values()
                            if job["status"] not in TERMINAL_STATES
                        ]
                    yield {
                        "kind": "status",
                        "runtime_installed": self._runtime_installed(),
                        "singing": dict(self._singing) if self._singing else None,
                        "active_jobs": active,
                    }
                await asyncio.sleep(interval)

        return stream()


def get_plugin() -> Plugin:
    return Plugin()
