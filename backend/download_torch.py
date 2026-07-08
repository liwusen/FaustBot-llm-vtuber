"""ASR + Torch 独立下载模块。

Phase B2: 参考 setup-runtime.bat 的 pip install 逻辑，用 Python 重写。
支持 Torch 版本选择、阿里云镜像、实时 pip 输出回调。
"""

from __future__ import annotations

import subprocess
import sys
import importlib.metadata
from pathlib import Path
import re
from typing import Callable

from faust_backend.logger import get_logger

log = get_logger("faust.download.torch")

# ── Torch 版本映射 ──

TORCH_VARIANTS = {
    "cu128": {"index_url": "https://download.pytorch.org/whl/cu128", "cuda": "12.8"},
    "cu121": {"index_url": "https://download.pytorch.org/whl/cu121", "cuda": "12.1"},
    "cu130": {"index_url": "https://download.pytorch.org/whl/cu130", "cuda": "13.0"},
    "cpu": {"index_url": "https://download.pytorch.org/whl/cpu", "cuda": None},
}

ALIYUN_MIRROR_TEMPLATE = "https://mirrors.aliyun.com/pytorch-wheels/{variant}/"


# ── pip 流式输出工具 ──

def _run_pip_streaming(args: list[str], progress_callback: Callable | None, stage_name: str, cancel_check: Callable[[], None] | None = None) -> int:
    """运行 pip 命令，实时推送 stdout/stderr 行，解析下载进度。"""
    log.info("Running pip command: %s", " ".join([sys.executable, "-m", "pip"] + args))
    process = subprocess.Popen(
        [sys.executable, "-m", "pip"] + args,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )
    download_count = 0
    expected_downloads = 20  # estimate per package batch
    download_re = re.compile(r"Downloading\s+\S+\s+\(([\d.]+)\s*(kB|MB)\)")
    if process.stdout:
        for line in process.stdout:
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    process.kill()
                    process.wait()
                    raise
            line = line.rstrip()
            if progress_callback:
                m = download_re.search(line)
                if m:
                    download_count += 1
                    pct = min(download_count / expected_downloads * 100, 99)
                    progress_callback("pip_download", pct, f"[{stage_name}] {line}")
                elif line.startswith("Installing collected packages:"):
                    progress_callback("pip_install", None, f"[{stage_name}] {line}")
                elif line.startswith("Successfully installed"):
                    progress_callback("pip_done", None, f"[{stage_name}] {line}")
                else:
                    progress_callback("pip_output", None, f"[{stage_name}] {line}")
    process.wait()
    return process.returncode


def _run_pip_simple(args: list[str]) -> tuple[int, str]:
    """运行 pip 命令，收集输出。"""
    log.info("Running pip command: %s", " ".join([sys.executable, "-m", "pip"] + args))
    result = subprocess.run(
        [sys.executable, "-m", "pip"] + args,
        capture_output=True, text=True, timeout=300,
    )
    return result.returncode, result.stdout + result.stderr


# ── Torch 卸载 ──


def uninstall_torch_if_needed(target_variant: str) -> bool:
    """检测当前 Torch 版本，如与目标不一致则卸载。返回 True 表示已卸载。

    使用 importlib.metadata 检查包版本，避免 import torch 触发耗时 DLL 加载。
    """
    try:
        tv = importlib.metadata.version("torch")
    except importlib.metadata.PackageNotFoundError:
        return False

    # 从版本字符串推断当前是 CPU 还是 CUDA: "2.1.0+cu121" / "2.1.0+cpu" / "2.1.0"
    current_is_cpu = True
    current_cuda = ""
    if "+" in tv:
        suffix = tv.split("+", 1)[1]
        if suffix == "cpu":
            current_is_cpu = True
        elif suffix.startswith("cu"):
            current_is_cpu = False
            current_cuda = suffix[2:]  # "121" from "cu121"

    target_cuda = (TORCH_VARIANTS.get(target_variant) or {}).get("cuda")

    if target_cuda is None:
        # 目标为 cpu — 如果当前有 CUDA 则需要卸载
        if not current_is_cpu:
            log.info("当前 Torch 为 GPU 版本，目标为 CPU，需卸载")
            rc, _ = _run_pip_simple(["uninstall", "torch", "torchvision", "torchaudio", "-y"])
            return rc == 0
        return False

    # 目标为 GPU — 检查 CUDA 版本是否匹配
    if current_is_cpu:
        log.info("当前 Torch 为 CPU 版本，目标为 GPU %s，需卸载重装", target_variant)
        rc, _ = _run_pip_simple(["uninstall", "torch", "torchvision", "torchaudio", "-y"])
        return rc == 0

    if target_cuda and current_cuda.startswith(str(target_cuda)[:4]):
        log.info("当前 Torch CUDA 版本 %s 与目标 %s 匹配，无需卸载", current_cuda, target_variant)
        return False

    log.info("当前 Torch CUDA 版本 %s 与目标 %s 不匹配，卸载重装", current_cuda, target_variant)
    rc, _ = _run_pip_simple(["uninstall", "torch", "torchvision", "torchaudio", "-y"])
    return rc == 0


def download_asr_models(
    progress_callback: Callable[[str, float | None, str], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    """下载 ASR 模型文件（FunAudioLLM/Fun-ASR-Nano-2512, fsmn-vad）。

    通过 FunASR 的 AutoModel 触发模型下载并缓存到本地。
    """
    log.info("下载 ASR 模型...")

    def _cb(stage: str, pct: float | None = None, msg: str = "") -> None:
        if progress_callback:
            progress_callback(stage, pct, msg)

    _cb("asr_model_start", 0, "开始下载 ASR 模型...")

    code = (
        'from funasr import AutoModel; '
        'AutoModel(model="FunAudioLLM/Fun-ASR-Nano-2512", vad_model="fsmn-vad", disable_update=True)'
    )

    process = subprocess.Popen(
        [sys.executable, "-c", code],
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, bufsize=1,
    )

    download_re = re.compile(r"Downloading\s+\S+\s+\(([\d.]+)\s*(kB|MB)\)")
    download_count = 0
    expected_downloads = 15

    if process.stdout:
        for line in process.stdout:
            if cancel_check:
                try:
                    cancel_check()
                except Exception:
                    process.kill()
                    process.wait()
                    raise
            line = line.rstrip()
            if not line:
                continue
            m = download_re.search(line)
            if m:
                download_count += 1
                pct = min(download_count / expected_downloads * 100, 95)
                _cb("asr_model_download", pct, line)
            elif "Downloading" in line or "100%" in line:
                _cb("asr_model_download", None, line)
            else:
                _cb("asr_model_output", None, line)

    process.wait()

    if process.returncode != 0:
        _cb("error", None, f"ASR 模型下载失败 (exit code {process.returncode})")
        return {"success": False, "error": f"ASR 模型下载失败 (exit code {process.returncode})"}

    _cb("asr_model_done", 100, "ASR 模型下载完成")
    return {"success": True, "error": None}

# ── 主安装函数 ──


def install_torch_and_funasr(
    torch_variant: str = "cpu",
    use_aliyun_mirror: bool = False,
    progress_callback: Callable[[str, float | None, str], None] | None = None,
    cancel_check: Callable[[], None] | None = None,
) -> dict:
    """安装 PyTorch + funasr。

    步骤：
    1. 卸载旧 Torch（如需）
    2. 安装 PyTorch（torch torchvision torchaudio）
    3. 安装 funasr
    4. 下载 ASR 模型
    5. 验证安装

    Args:
        torch_variant: cu128|cu121|cu130|cpu
        use_aliyun_mirror: 是否使用阿里云镜像
        progress_callback: (stage, percent, message) 进度回调

    Returns:
        {"success": bool, "error": str | None}
    """
    variant = TORCH_VARIANTS.get(torch_variant)
    if not variant:
        return {"success": False, "error": f"不支持的 Torch 版本: {torch_variant}"}

    def _cb(stage: str, pct: float | None = None, msg: str = "") -> None:
        if progress_callback:
            progress_callback(stage, pct, msg)

    index_url: str = str(variant["index_url"])

    # ── 1. 卸载旧 Torch（如需） ──
    _cb("torch_start", 0, f"准备安装 PyTorch ({torch_variant})...")
    try:
        uninstall_torch_if_needed(torch_variant)
    except Exception as e:
        log.warning("卸载旧 Torch 时出错: %s", e)

    # ── 2. 安装 PyTorch ──
    _cb("torch_start", 5, f"开始安装 PyTorch ({torch_variant})...")

    pip_args: list[str] = ["install"]

    if use_aliyun_mirror:
        mirror_url = ALIYUN_MIRROR_TEMPLATE.format(variant=torch_variant)
        pip_args.extend(["-f", mirror_url])
        pip_packages = ["torch", "torchvision", "torchaudio"]
    else:
        pip_args.extend(["--index-url", index_url])
        # 对于 CPU 版本，使用默认索引
        pip_packages = ["torch", "torchvision", "torchaudio"]

    rc = _run_pip_streaming(
        pip_args + pip_packages,
        progress_callback,
        "torch",
        cancel_check,
    )

    if rc != 0:
        _cb("error", None, f"PyTorch 安装失败 (exit code {rc})")
        return {"success": False, "error": f"PyTorch 安装失败 (exit code {rc})"}

    _cb("torch_done", 60, "PyTorch 安装完成")

    # ── 3. 安装 funasr ──
    _cb("funasr_start", 60, "开始安装 funasr...")

    rc = _run_pip_streaming(
        ["install", "funasr>=0.9.6"],
        progress_callback,
        "funasr",
        cancel_check,
    )

    if rc != 0:
        _cb("error", None, f"funasr 安装失败 (exit code {rc})")
        return {"success": False, "error": f"funasr 安装失败 (exit code {rc})"}


    # ── 5. 下载 ASR 模型 ──
    _cb("asr_model_start", 85, "开始下载 ASR 模型...")
    try:
        if cancel_check:
            cancel_check()
        result = download_asr_models(progress_callback, cancel_check)
        if not result.get("success"):
            _cb("error", None, f"ASR 模型下载失败: {result.get('error', '')}")
            return result
    except Exception as e:
        log.warning("ASR 模型下载出错（可跳过）: %s", e)
        _cb("asr_model_warn", None, f"ASR 模型下载出错（可跳过）: {e}")
    _cb("funasr_done", 100, "funasr 安装完成")

    # ── 4. 验证安装 ──
    _cb("verify", None, "验证安装...")
    try:
        import funasr  # noqa: F401
        version = getattr(funasr, "__version__", "unknown")
        _cb("complete", 100, f"ASR 组件安装完成 (funasr {version})")
    except ImportError as e:
        _cb("error", None, f"验证安装失败: {e}")
        return {"success": False, "error": f"funasr 导入失败: {e}"}

    return {"success": True, "error": None}
