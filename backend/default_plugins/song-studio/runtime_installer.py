"""sva-runtime 独立 Python 运行时安装（embeddable zip + pip，主环境运行）。

主环境是嵌入式 Python（不支持 -m venv），因此采用与 setup-runtime.bat 相同的
embeddable zip 方案，安装到 ~/.faustbot/plugin_data/sva-runtime。
"""
from __future__ import annotations

import os
import re
import shutil
import subprocess
import threading
import time
import zipfile
from pathlib import Path
from typing import Callable

import requests

from faust_backend.utils import DownloadTask

PYTHON_EMBED_URL = "https://www.python.org/ftp/python/3.11.9/python-3.11.9-embed-amd64.zip"
GET_PIP_URL = "https://bootstrap.pypa.io/get-pip.py"
TORCH_ALIYUN_FIND_LINKS_TMPL = "https://mirrors.aliyun.com/pytorch-wheels/{variant}/"
TORCH_OFFICIAL_INDEX_TMPL = "https://download.pytorch.org/whl/{variant}"
PIP_ALIYUN_INDEX = "https://mirrors.aliyun.com/pypi/simple/"

# 各 CUDA 变体对应的 torch 版本组合（cp311 win_amd64 在阿里云镜像与官方源均有）
# cu121 支持 sm_50~sm_90（GTX10xx~RTX40xx）；RTX50xx (sm_120) 需要 cu128+
TORCH_VARIANTS = {
    "cu121": {"torch": "2.4.0", "torchaudio": "2.4.0", "torchvision": "0.19.0"},
    "cu128": {"torch": "2.7.1", "torchaudio": "2.7.1", "torchvision": "0.22.1"},
    "cu130": {"torch": "2.9.1", "torchaudio": "2.9.1", "torchvision": "0.24.1"},
}
DEFAULT_TORCH_VARIANT = "cu121"
SEEDVC_COMMIT = "51383efd921027683c89e5348211d93ff12ac2a8"
SEEDVC_ZIP_NAME = f"seed-vc-{SEEDVC_COMMIT}.zip"
# GitHub archive 无 Content-Length 且 gh-proxy 会截断，固定使用 modelscope 托管的源码包
SEEDVC_ZIP_URL = f"https://modelscope.cn/datasets/allenlee18/FaustBotData/resolve/master/{SEEDVC_ZIP_NAME}"
RUNTIME_VERSION = "1"

# Seed-VC 推理所需依赖（排除 gradio/GUI/评测类）
SEEDVC_REQUIREMENTS = [
    "scipy==1.13.1",
    "librosa==0.10.2",
    "huggingface-hub>=0.28.1",
    "munch==4.0.0",
    "einops==0.8.0",
    "descript-audio-codec==1.0.0",
    "pydub==0.25.1",
    "transformers==4.46.3",
    "soundfile==0.12.1",
    "numpy==1.26.4",
    "hydra-core==1.3.2",
    "pyyaml",
    "python-dotenv",
]
# audio-separator 必须单独安装：其 onnx 依赖要求 protobuf>=3.20.2，而
# descript-audiotools 锁 protobuf<3.20，同一次 pip 求解无解；分两次安装后
# protobuf 被升到 4.x，仅影响 audiotools 的 tensorboard 训练路径，推理不受影响。
# 0.31+ 要求 numpy>=2，与 Seed-VC 的 numpy==1.26.4 冲突。
# 安装时会附加 torch/torchvision 版本锁：onnx2torch 依赖 torchvision（仅锁 >=0.9），
# 不 pin 会把 torch 升级成最新 CPU 版。
SEPARATOR_REQUIREMENTS = ["audio-separator[gpu]>=0.28.0,<0.31"]

ProgressFn = Callable[[str, float, str], None]


class RuntimeInstallError(RuntimeError):
    pass


def runtime_python(runtime_dir: Path) -> Path:
    return runtime_dir / "python.exe"


def seedvc_dir(runtime_dir: Path) -> Path:
    return runtime_dir / "seed-vc"


def is_installed(runtime_dir: Path) -> bool:
    marker = runtime_dir / "sva-runtime.version"
    return (
        runtime_python(runtime_dir).exists()
        and seedvc_dir(runtime_dir).joinpath("inference.py").exists()
        and marker.exists()
        and marker.read_text(encoding="utf-8").strip() == RUNTIME_VERSION
    )


def _download(url: str, dest: Path, progress: ProgressFn, stage: str,
              cancel_check: Callable[[], bool] | None = None) -> None:
    dest.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=60) as resp:
        resp.raise_for_status()
        total = int(resp.headers.get("content-length") or 0)
        done = 0
        last_emit = 0.0
        with open(dest, "wb") as f:
            for chunk in resp.iter_content(chunk_size=1024 * 256):
                if cancel_check and cancel_check():
                    raise RuntimeInstallError("安装已取消")
                f.write(chunk)
                done += len(chunk)
                now = time.monotonic()
                if now - last_emit < 0.5:
                    continue
                last_emit = now
                if total:
                    progress(stage, done * 100.0 / total, f"{done // 1024}KB / {total // 1024}KB")
                else:
                    progress(stage, -1, f"已下载 {done // 1024}KB")


_PIP_RAW_PROGRESS = re.compile(r"^Progress (\d+) of (\d+)")


def _download_multithread(url: str, dest: Path, progress: ProgressFn, stage: str,
                          cancel_check: Callable[[], bool] | None = None) -> None:
    """用 DownloadTask 多线程分块下载，轮询任务状态上报进度。"""
    dest.parent.mkdir(parents=True, exist_ok=True)
    task = DownloadTask(url=url, dest_path=dest, tag=stage, asset_name=dest.name)
    worker = threading.Thread(target=task.run, daemon=True)
    worker.start()
    last_emit = 0.0
    while worker.is_alive():
        if cancel_check and cancel_check():
            raise RuntimeInstallError("安装已取消")
        worker.join(timeout=0.25)
        now = time.monotonic()
        if now - last_emit >= 0.5 and task.total_bytes:
            last_emit = now
            progress(stage, task.progress * 100.0,
                     f"{task.downloaded_bytes // (1024 * 1024)}MB / "
                     f"{task.total_bytes // (1024 * 1024)}MB ({task.speed_mbps:.1f}MB/s)")
    if task.error or not task.done:
        # DownloadTask 的分块临时文件命名为 <stem>.partN
        for part in dest.parent.glob(dest.stem + ".part*"):
            part.unlink(missing_ok=True)
        raise RuntimeError(f"多线程下载失败: {task.error or '未完成'}")
    progress(stage, 100.0, f"{task.total_bytes // (1024 * 1024)}MB 下载完成")


def _run_pip(python_exe: Path, args: list[str], progress: ProgressFn, stage: str,
             cancel_check: Callable[[], bool] | None = None) -> None:
    # pip 检测到非 TTY 时会隐藏进度条，用 raw 模式输出可解析的 "Progress N of M" 行
    if args and args[0] == "install":
        args = [args[0], "--progress-bar", "raw", *args[1:]]
    cmd = [str(python_exe), "-m", "pip"] + args
    # 隔离用户全局 site-packages（Roaming 下的包会污染依赖求解）
    env = {**os.environ, "PYTHONNOUSERSITE": "1"}
    proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT, env=env,
        creationflags=subprocess.CREATE_NEW_CONSOLE if os.name == "nt" else 0)
    assert proc.stdout is not None
    fd = proc.stdout.fileno()
    buf = b""
    last_progress_emit = 0.0
    while True:
        data = os.read(fd, 4096)
        if cancel_check and cancel_check():
            proc.kill()
            raise RuntimeInstallError("安装已取消")
        if not data:
            break
        buf += data
        while True:
            # \r（进度刷新）与 \n 都视为行结束；\r\n 是普通换行（Windows 文本模式）
            idx_n = buf.find(b"\n")
            idx_r = buf.find(b"\r")
            if idx_n == -1 and idx_r == -1:
                break
            if idx_r != -1 and (idx_n == -1 or idx_r < idx_n):
                if idx_r == len(buf) - 1 and data:
                    break  # \r 在缓冲区末尾，等下一块数据判断是否为 \r\n
                seg, rest = buf[:idx_r], buf[idx_r + 1:]
                if rest[:1] == b"\n":
                    buf = rest[1:]
                    is_refresh = False
                else:
                    buf = rest
                    is_refresh = True
            else:
                seg, buf = buf[:idx_n], buf[idx_n + 1:]
                is_refresh = False
            text = seg.decode("utf-8", errors="replace").strip()
            if not text:
                continue
            m = _PIP_RAW_PROGRESS.match(text)
            if m or is_refresh:
                now = time.monotonic()
                if now - last_progress_emit < 0.5:
                    continue
                last_progress_emit = now
                if m:
                    done, total = int(m.group(1)), int(m.group(2))
                    pct = done * 100.0 / total if total else -1
                    progress(stage, pct, f"{done // (1024 * 1024)}MB / {total // (1024 * 1024)}MB")
                    continue
            progress(stage, -1, text)
    if buf.strip():
        progress(stage, -1, buf.decode("utf-8", errors="replace").strip())
    code = proc.wait()
    if code != 0:
        raise RuntimeInstallError(f"pip 失败 (exit {code}): {' '.join(args[:4])} ...")


def _install_seedvc_source(runtime_dir: Path, progress: ProgressFn,
                           cancel_check: Callable[[], bool] | None,
                           local_zip: Path | None = None) -> None:
    target = seedvc_dir(runtime_dir)
    if target.exists():
        shutil.rmtree(target)
    zip_path = runtime_dir / "seed-vc-src.zip"
    zip_ready = False
    if local_zip is not None and local_zip.exists():
        if zipfile.is_zipfile(local_zip):
            progress("download_seedvc", 0, f"使用本地缓存的 seed-vc 源码包: {local_zip}")
            runtime_dir.mkdir(parents=True, exist_ok=True)
            shutil.copyfile(local_zip, zip_path)
            zip_ready = True
        else:
            progress("download_seedvc", -1, f"本地缓存包损坏，忽略并重新下载: {local_zip}")
    if not zip_ready:
        last_error: Exception | None = None
        for attempt in (1, 2):
            try:
                progress("download_seedvc", 0,
                         f"下载 seed-vc 源码 (尝试 {attempt}/2): {SEEDVC_ZIP_URL}")
                try:
                    _download_multithread(SEEDVC_ZIP_URL, zip_path, progress,
                                          "download_seedvc", cancel_check)
                except RuntimeInstallError:
                    raise
                except Exception as exc:
                    progress("download_seedvc", -1, f"多线程下载不可用（{exc}），回退单线程下载")
                    _download(SEEDVC_ZIP_URL, zip_path, progress, "download_seedvc", cancel_check)
                if not zipfile.is_zipfile(zip_path):
                    raise RuntimeError("压缩包不完整（下载可能被截断）")
                last_error = None
                break
            except RuntimeInstallError:
                raise
            except Exception as exc:
                last_error = exc
                progress("download_seedvc", -1, f"下载失败: {exc}")
        if last_error is not None:
            raise RuntimeInstallError(f"seed-vc 源码下载失败: {last_error}")

    progress("download_seedvc", 90, "解压 seed-vc 源码...")
    extract_root = runtime_dir / "seed-vc-src-tmp"
    if extract_root.exists():
        shutil.rmtree(extract_root)
    with zipfile.ZipFile(zip_path) as zf:
        zf.extractall(extract_root)
    zip_path.unlink()
    # GitHub archive zip 顶层是 seed-vc-<commit>/ 目录
    subdirs = [p for p in extract_root.iterdir() if p.is_dir()]
    if len(subdirs) != 1 or not subdirs[0].joinpath("inference.py").exists():
        shutil.rmtree(extract_root)
        raise RuntimeInstallError("seed-vc 源码压缩包结构异常（缺少 inference.py）")
    for junk in ("examples", "assets", "__pycache__"):
        junk_dir = subdirs[0] / junk
        if junk_dir.exists():
            shutil.rmtree(junk_dir)
    subdirs[0].rename(target)
    extract_root.rmdir()


def install_runtime(runtime_dir: Path, progress: ProgressFn,
                    cancel_check: Callable[[], bool] | None = None,
                    use_pypi_mirror: bool = True,
                    local_seedvc_zip: Path | None = None,
                    torch_variant: str = DEFAULT_TORCH_VARIANT) -> None:
    """完整安装流程。progress(stage, percent(-1 表示日志行), message)。"""
    if torch_variant not in TORCH_VARIANTS:
        raise RuntimeInstallError(
            f"未知 torch 变体: {torch_variant}（可选: {', '.join(TORCH_VARIANTS)}）")
    pins = TORCH_VARIANTS[torch_variant]
    runtime_dir.mkdir(parents=True, exist_ok=True)
    python_exe = runtime_python(runtime_dir)

    if not python_exe.exists():
        progress("download_python", 0, "下载 Python embeddable...")
        zip_path = runtime_dir / "python-embed.zip"
        _download(PYTHON_EMBED_URL, zip_path, progress, "download_python", cancel_check)
        progress("extract_python", 50, "解压 Python...")
        with zipfile.ZipFile(zip_path) as zf:
            zf.extractall(runtime_dir)
        zip_path.unlink()

    # 打开 site-packages 支持（embeddable 默认关闭 site），并把 seed-vc 加入 sys.path：
    # 存在 ._pth 时 Python 进入隔离模式，cwd/PYTHONPATH 均不生效，
    # inference.py 依赖的 modules 包必须在此声明才能被找到
    pth_file = runtime_dir / "python311._pth"
    if not pth_file.exists():
        raise RuntimeInstallError(f"缺少 {pth_file}，Python embeddable 解压异常")
    expected_pth = "python311.zip\n.\nLib\\site-packages\nseed-vc\nimport site\n"
    if pth_file.read_text(encoding="utf-8") != expected_pth:
        pth_file.write_text(expected_pth, encoding="utf-8")

    if not (runtime_dir / "Lib" / "site-packages" / "pip").exists():
        progress("get_pip", 0, "下载 get-pip.py...")
        get_pip = runtime_dir / "get-pip.py"
        _download(GET_PIP_URL, get_pip, progress, "get_pip", cancel_check)
        progress("get_pip", 60, "安装 pip...")
        result = subprocess.run(
            [str(python_exe), str(get_pip), "--no-warn-script-location"],
            capture_output=True, text=True, encoding="utf-8", errors="replace",
            env={**os.environ, "PYTHONNOUSERSITE": "1"},
            creationflags=getattr(subprocess, "CREATE_NO_WINDOW", 0))
        if result.returncode != 0:
            raise RuntimeInstallError(f"pip 安装失败: {result.stdout[-800:]}{result.stderr[-800:]}")
        get_pip.unlink()

    progress("install_torch", 0, f"安装 torch {pins['torch']} ({torch_variant})...")
    torch_args = ["install", f"torch=={pins['torch']}", f"torchaudio=={pins['torchaudio']}",
                  "--no-warn-script-location"]
    if use_pypi_mirror:
        torch_args += ["-f", TORCH_ALIYUN_FIND_LINKS_TMPL.format(variant=torch_variant),
                       "-i", PIP_ALIYUN_INDEX]
    else:
        torch_args += ["--index-url", TORCH_OFFICIAL_INDEX_TMPL.format(variant=torch_variant)]
    _run_pip(python_exe, torch_args, progress, "install_torch", cancel_check)

    progress("install_deps", 0, "安装 Seed-VC 依赖...")
    deps_args = ["install", *SEEDVC_REQUIREMENTS, "--no-warn-script-location"]
    if use_pypi_mirror:
        deps_args += ["-i", PIP_ALIYUN_INDEX]
    _run_pip(python_exe, deps_args, progress, "install_deps", cancel_check)

    progress("install_separator", 0, "安装 audio-separator...")
    sep_args = ["install", *SEPARATOR_REQUIREMENTS,
                f"torch=={pins['torch']}", f"torchvision=={pins['torchvision']}",
                "--no-warn-script-location"]
    if use_pypi_mirror:
        sep_args += ["-f", TORCH_ALIYUN_FIND_LINKS_TMPL.format(variant=torch_variant),
                     "-i", PIP_ALIYUN_INDEX]
    else:
        sep_args += ["--extra-index-url", TORCH_OFFICIAL_INDEX_TMPL.format(variant=torch_variant)]
    _run_pip(python_exe, sep_args, progress, "install_separator", cancel_check)

    _install_seedvc_source(runtime_dir, progress, cancel_check, local_zip=local_seedvc_zip)

    (runtime_dir / "torch-variant.txt").write_text(torch_variant, encoding="utf-8")
    (runtime_dir / "sva-runtime.version").write_text(RUNTIME_VERSION, encoding="utf-8")
    progress("done", 100, "sva-runtime 安装完成")
