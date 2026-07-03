"""
本地 TTS 包下载与解压模块。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
from pathlib import Path
from typing import Callable

import py7zr
import requests
from tqdm import tqdm


BACKEND_DIR = Path(__file__).resolve().parent
TTS_HUB_DIR = BACKEND_DIR / "tts-hub"
DOWNLOAD_DIR = TTS_HUB_DIR / ".downloads"

NVIDIA50_URL = "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250604-nvidia50.7z"
STANDARD_URL = "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v2pro-20250604.7z"


def ask_yes_no(prompt: str) -> bool:
    while True:
        answer = input(f"{prompt} [y/n]: ").strip().lower()
        if answer in {"y", "yes"}:
            return True
        if answer in {"n", "no"}:
            return False
        print("请输入 y 或 n。")


def choose_download() -> tuple[str, str]:
    is_nvidia50 = ask_yes_no("是否为 50 系列显卡？")
    if is_nvidia50:
        return NVIDIA50_URL, "GPT-SoVITS-v2pro-20250604-nvidia50.7z"
    return STANDARD_URL, "GPT-SoVITS-v2pro-20250604.7z"


def choose_download_by_variant(variant: str | None) -> tuple[str, str]:
    normalized = str(variant or "").strip().lower()
    if normalized == "nvidia50":
        return NVIDIA50_URL, "GPT-SoVITS-v2pro-20250604-nvidia50.7z"
    if normalized == "standard":
        return STANDARD_URL, "GPT-SoVITS-v2pro-20250604.7z"
    return choose_download()


def download_with_progress(
    url: str,
    archive_path: Path,
    progress_callback: Callable[[str, float, str], None] | None = None,
) -> None:
    """下载文件，支持进度回调。

    progress_callback(stage, percent, message):
    - stage: "download" | "extract" | "normalize" | "complete" | "error"
    - percent: 0.0-100.0
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    with requests.get(url, stream=True, timeout=600) as response:
        response.raise_for_status()
        total = int(response.headers.get("content-length", 0) or 0)
        downloaded = 0

        if progress_callback:
            progress_callback("download", 0.0, f"开始下载 {archive_path.name}")

        with archive_path.open("wb") as file_obj, tqdm(
            total=total,
            unit="B",
            unit_scale=True,
            unit_divisor=1024,
            desc=f"Downloading {archive_path.name}",
        ) as progress:
            for chunk in response.iter_content(chunk_size=1024 * 1024):
                if not chunk:
                    continue
                file_obj.write(chunk)
                progress.update(len(chunk))
                downloaded += len(chunk)
                if total > 0 and progress_callback:
                    pct = min(100.0, round(downloaded / total * 100, 1))
                    progress_callback("download", pct, f"下载中 {pct:.1f}% — {archive_path.name}")


def extract_with_progress(
    archive_path: Path,
    destination_dir: Path,
    progress_callback: Callable[[str, float, str], None] | None = None,
) -> None:
    """解压文件，支持进度回调。

    注意：7z 为固态压缩格式，逐个文件解压会重复解整个固态块。
    采用 extractall() 一次性解压全部文件。
    """
    destination_dir.mkdir(parents=True, exist_ok=True)

    if progress_callback:
        progress_callback("extract", 0.0, f"开始解压 {archive_path.name}")

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        # 固态压缩下 extractall 比逐个 extract 快 10-100 倍
        archive.extractall(path=destination_dir)

    if progress_callback:
        progress_callback("extract", 100.0, "解压完成")


def normalize_tts_layout(
    progress_callback: Callable[[str, float, str], None] | None = None,
) -> None:
    """修正嵌套目录结构。"""
    nested_50 = TTS_HUB_DIR / "GPT-SoVITS-v2pro-20250604-nvidia50" / "GPT-SoVITS-v2pro-20250604-nvidia50"
    nested_std = TTS_HUB_DIR / "GPT-SoVITS-v2pro-20250604" / "GPT-SoVITS-v2pro-20250604"

    if progress_callback:
        progress_callback("normalize", 90.0, "修正目录结构...")

    for nested in (nested_50, nested_std):
        if not nested.exists():
            continue
        for item in nested.iterdir():
            target = nested.parent / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))

    if progress_callback:
        progress_callback("complete", 100.0, "TTS 下载并解压完成")


def download_and_extract_tts(
    variant: str,
    progress_callback: Callable[[str, float, str], None] | None = None,
) -> None:
    """完整流程：选择 → 下载 → 解压 → 修正布局。"""
    url, filename = choose_download_by_variant(variant)
    archive_path = DOWNLOAD_DIR / filename

    download_with_progress(url, archive_path, progress_callback)
    extract_with_progress(archive_path, TTS_HUB_DIR, progress_callback)
    normalize_tts_layout(progress_callback)


async def download_tts_async(
    variant: str,
    progress_callback: Callable[[str, float, str], None] | None = None,
) -> None:
    """异步包装，供 API 调用。"""
    loop = asyncio.get_event_loop()
    await loop.run_in_executor(None, download_and_extract_tts, variant, progress_callback)


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并解压本地 TTS 包")
    parser.add_argument("--gpu-variant", choices=["nvidia50", "standard"], default=None)
    args = parser.parse_args()

    try:
        url, filename = choose_download_by_variant(args.gpu_variant)
        archive_path = DOWNLOAD_DIR / filename
        print(f"下载地址: {url}")
        download_with_progress(url, archive_path)
        extract_with_progress(archive_path, TTS_HUB_DIR)
        normalize_tts_layout()
        print("TTS 下载并解压完成。")
        return 0
    except KeyboardInterrupt:
        print("用户取消。")
        return 1
    except Exception as exc:
        print(f"download_tts 失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
