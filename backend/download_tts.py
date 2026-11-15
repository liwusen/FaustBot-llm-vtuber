"""
本地 TTS 包下载、解压与 V5 增量升级模块。
"""

from __future__ import annotations

import argparse
import asyncio
import shutil
import zipfile
from pathlib import Path, PurePosixPath
from typing import Callable

import py7zr
import requests
from tqdm import tqdm


BACKEND_DIR = Path(__file__).resolve().parent
TTS_HUB_DIR = BACKEND_DIR / "tts-hub"
DOWNLOAD_DIR = TTS_HUB_DIR / ".downloads"

V5_STANDARD_URL = "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v5-20261005.7z"
V5_NVIDIA50_URL = "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS-v5-20261005nvidia50x0.7z"
V5_UPDATE_URL = "https://www.modelscope.cn/models/FlowerCry/gpt-sovits-7z-pacakges/resolve/master/GPT-SoVITS_v5update20261006.zip"

V5_UPDATE_ARCHIVE = "GPT-SoVITS_v5update20261006.zip"

ProgressCallback = Callable[[str, float, str], None] | None
CancelCheck = Callable[[], None] | None


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
        return V5_NVIDIA50_URL, "GPT-SoVITS-v5-20261005nvidia50x0.7z"
    return V5_STANDARD_URL, "GPT-SoVITS-v5-20261005.7z"


def choose_download_by_variant(variant: str | None) -> tuple[str, str]:
    normalized = str(variant or "").strip().lower()
    if normalized == "nvidia50":
        return V5_NVIDIA50_URL, "GPT-SoVITS-v5-20261005nvidia50x0.7z"
    if normalized == "standard":
        return V5_STANDARD_URL, "GPT-SoVITS-v5-20261005.7z"
    return choose_download()


def download_with_progress(
    url: str,
    archive_path: Path,
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> None:
    """下载文件，支持进度回调。

    progress_callback(stage, percent, message):
    - stage: "download" | "extract" | "normalize" | "verify" | "complete" | "error"
    - percent: 0.0-100.0
    """
    archive_path.parent.mkdir(parents=True, exist_ok=True)
    if cancel_check:
        cancel_check()
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
                if cancel_check:
                    cancel_check()
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
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> None:
    """解压 7z 文件，支持进度回调。

    注意：7z 为固态压缩格式，逐个文件解压会重复解整个固态块。
    采用 extractall() 一次性解压全部文件。
    """
    destination_dir.mkdir(parents=True, exist_ok=True)
    if cancel_check:
        cancel_check()

    if progress_callback:
        progress_callback("extract", 0.0, f"开始解压 {archive_path.name}")

    with py7zr.SevenZipFile(archive_path, mode="r") as archive:
        # 固态压缩下 extractall 比逐个 extract 快 10-100 倍
        if cancel_check:
            cancel_check()
        archive.extractall(path=destination_dir)

    if progress_callback:
        progress_callback("extract", 100.0, "解压完成")


def normalize_tts_layout(
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> None:
    """修正整合包多套一层的目录结构。"""
    if progress_callback:
        progress_callback("normalize", 90.0, "修正目录结构...")

    if not TTS_HUB_DIR.is_dir():
        return

    for parent in sorted(TTS_HUB_DIR.iterdir(), key=lambda p: p.name.lower()):
        if cancel_check:
            cancel_check()
        if not parent.is_dir() or parent.name.startswith(".") or (parent / "api.py").is_file():
            continue
        nested = [
            child
            for child in sorted(parent.iterdir(), key=lambda p: p.name.lower())
            if child.is_dir() and not child.name.startswith(".") and (child / "api.py").is_file()
        ]
        if len(nested) != 1:
            continue
        inner = nested[0]
        for item in inner.iterdir():
            target = parent / item.name
            if target.exists():
                continue
            shutil.move(str(item), str(target))


def _v5_paths() -> tuple[Path, Path, Path, Path]:
    from faust_backend.services import tts_install

    return (
        tts_install.V5_MODELS_MODULE,
        tts_install.V5_TURBO_WEIGHT,
        tts_install.V5_DEV_WEIGHT,
        tts_install.V5_GPT_WEIGHT,
    )


def verify_v5_install(root: Path) -> None:
    """确认安装根具备 V5 代码与至少一个 V5 底模。缺任何一项都显式报错。"""
    models_module, turbo, dev, gpt = _v5_paths()
    required = [root / "api.py", root / models_module, root / gpt]
    missing = [path for path in required if not path.is_file()]
    if not (root / turbo).is_file() and not (root / dev).is_file():
        missing.append(root / turbo)
        missing.append(root / dev)
    if missing:
        joined = "\n".join(f"  - {path}" for path in missing)
        raise RuntimeError(f"V5 安装校验失败，缺少以下文件：\n{joined}")


def _safe_zip_target(root: Path, member_name: str) -> Path:
    normalized = str(member_name or "").replace("\\", "/")
    pure = PurePosixPath(normalized)
    if (
        pure.is_absolute()
        or ".." in pure.parts
        or (pure.parts and str(pure.parts[0]).endswith(":"))
    ):
        raise RuntimeError(f"增量包包含非法路径: {member_name}")
    root_resolved = root.resolve()
    target = (root / Path(*pure.parts)).resolve()
    if target != root_resolved and root_resolved not in target.parents:
        raise RuntimeError(f"增量包路径越界: {member_name}")
    return target


def _is_symlink_member(info: zipfile.ZipInfo) -> bool:
    return (info.external_attr >> 16) & 0o170000 == 0o120000


def _backup_zip_targets(
    root: Path,
    archive: zipfile.ZipFile,
) -> tuple[Path, list[Path], list[Path]]:
    """备份将被覆盖的文件，记录本次新增文件，供失败回滚。"""
    backup_dir = root / ".v5-upgrade-backup"
    if backup_dir.exists():
        shutil.rmtree(backup_dir)
    backup_dir.mkdir(parents=True)

    overwritten: list[Path] = []
    created: list[Path] = []
    for info in archive.infolist():
        if info.is_dir() or _is_symlink_member(info):
            continue
        target = _safe_zip_target(root, info.filename)
        try:
            relative = target.relative_to(root.resolve())
        except ValueError:
            relative = Path(info.filename)
        if target.exists():
            destination = backup_dir / relative
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(target, destination)
            overwritten.append(relative)
        else:
            created.append(target)
    return backup_dir, overwritten, created


def _rollback_upgrade(
    root: Path,
    backup_dir: Path,
    overwritten: list[Path],
    created: list[Path],
) -> None:
    root_resolved = root.resolve()
    for relative in overwritten:
        source = backup_dir / relative
        destination = root_resolved / relative
        if source.is_file():
            destination.parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(source, destination)
    for path in reversed(created):
        try:
            if path.is_file():
                path.unlink()
        except OSError:
            pass


def extract_zip_over(
    archive_path: Path,
    root: Path,
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> tuple[Path, list[Path], list[Path]]:
    """把增量 zip 安全解压到现有安装根，支持逐块进度与取消。

    返回 (备份目录, 被覆盖文件, 本次新增文件)，供调用方在后续校验失败时回滚。
    """
    with zipfile.ZipFile(archive_path, "r") as archive:
        backup_dir, overwritten, created = _backup_zip_targets(root, archive)
        members = [
            info
            for info in archive.infolist()
            if not info.is_dir() and not _is_symlink_member(info)
        ]
        total_bytes = sum(info.file_size for info in members) or 1
        copied_bytes = 0

        try:
            for info in members:
                if cancel_check:
                    cancel_check()
                target = _safe_zip_target(root, info.filename)
                target.parent.mkdir(parents=True, exist_ok=True)
                with archive.open(info, "r") as source, target.open("wb") as destination:
                    while True:
                        if cancel_check:
                            cancel_check()
                        chunk = source.read(4 * 1024 * 1024)
                        if not chunk:
                            break
                        destination.write(chunk)
                        copied_bytes += len(chunk)
                        if progress_callback:
                            pct = min(99.0, 85.0 + copied_bytes / total_bytes * 14.0)
                            progress_callback(
                                "extract",
                                pct,
                                f"解压 {info.filename} ({pct:.0f}%)",
                            )
        except BaseException:
            _rollback_upgrade(root, backup_dir, overwritten, created)
            raise
        return backup_dir, overwritten, created


def upgrade_to_v5(
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> None:
    """把 V5 增量包覆盖到已安装的 GPT-SoVITS 根目录。"""
    from faust_backend.services import tts_install

    def _progress(stage: str, percent: float, message: str) -> None:
        if not progress_callback:
            return
        if stage == "download":
            percent = percent * 0.85
        progress_callback(stage, percent, message)

    install = tts_install.find_tts_install(TTS_HUB_DIR)
    if install is None:
        raise FileNotFoundError(
            "未检测到可升级的 GPT-SoVITS 安装：请先下载 V5 全量包"
        )
    if install.is_v5:
        raise RuntimeError("当前安装已是 V5，无需增量升级")

    archive_path = DOWNLOAD_DIR / V5_UPDATE_ARCHIVE
    if progress_callback:
        progress_callback("download", 0.0, "开始下载 V5 增量包")
    download_with_progress(V5_UPDATE_URL, archive_path, _progress, cancel_check)

    if progress_callback:
        progress_callback("extract", 85.0, f"开始解压 {V5_UPDATE_ARCHIVE}")
    backup_dir, overwritten, created = extract_zip_over(
        archive_path, install.root, progress_callback, cancel_check
    )

    try:
        if progress_callback:
            progress_callback("verify", 99.0, "校验 V5 安装...")
        verify_v5_install(install.root)
    except BaseException:
        _rollback_upgrade(install.root, backup_dir, overwritten, created)
        raise
    if progress_callback:
        progress_callback("complete", 100.0, "V5 增量升级完成")


def download_and_extract_tts(
    variant: str,
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
) -> None:
    """完整流程：选择 → 下载 → 解压 → 修正布局 → 校验 V5。"""

    def _progress(stage: str, percent: float, message: str) -> None:
        if not progress_callback:
            return
        if stage == "download":
            percent = percent * 0.8
        elif stage == "extract":
            percent = 80.0 + percent * 0.08
        progress_callback(stage, percent, message)

    url, filename = choose_download_by_variant(variant)
    archive_path = DOWNLOAD_DIR / filename

    download_with_progress(url, archive_path, _progress, cancel_check)
    extract_with_progress(archive_path, TTS_HUB_DIR, _progress, cancel_check)
    normalize_tts_layout(_progress, cancel_check)

    if progress_callback:
        progress_callback("verify", 96.0, "校验 V5 安装...")
    from faust_backend.services import tts_install

    expected_root = TTS_HUB_DIR / archive_path.stem
    install = tts_install.find_tts_install(TTS_HUB_DIR)
    if (expected_root / "api.py").is_file():
        verify_v5_install(expected_root)
    elif install is not None:
        verify_v5_install(install.root)
    else:
        raise RuntimeError("V5 全量包解压后未找到含 api.py 的安装目录")
    if progress_callback:
        progress_callback("complete", 100.0, "V5 全量包安装完成")


async def download_tts_async(
    variant: str,
    progress_callback: ProgressCallback = None,
    cancel_check: CancelCheck = None,
    action: str = "install",
) -> None:
    """异步包装，供 API 调用。"""
    normalized = str(action or "install").strip().lower()
    if normalized == "upgrade":
        await asyncio.to_thread(upgrade_to_v5, progress_callback, cancel_check)
        return
    if normalized != "install":
        raise ValueError(f"未知 TTS 安装动作: {action}")
    await asyncio.to_thread(
        download_and_extract_tts, variant, progress_callback, cancel_check
    )


def main() -> int:
    parser = argparse.ArgumentParser(description="下载并解压本地 TTS 包")
    parser.add_argument("--gpu-variant", choices=["nvidia50", "standard"], default=None)
    parser.add_argument("--action", choices=["install", "upgrade"], default="install")
    args = parser.parse_args()

    try:
        if args.action == "upgrade":
            upgrade_to_v5()
            print("V5 增量升级完成。")
        else:
            url, filename = choose_download_by_variant(args.gpu_variant)
            archive_path = DOWNLOAD_DIR / filename
            print(f"下载地址: {url}")
            download_with_progress(url, archive_path)
            extract_with_progress(archive_path, TTS_HUB_DIR)
            normalize_tts_layout()
            from faust_backend.services import tts_install

            install = tts_install.find_tts_install(TTS_HUB_DIR)
            if install is None:
                raise RuntimeError("解压后未找到 GPT-SoVITS 安装目录")
            verify_v5_install(install.root)
            print("V5 全量包安装完成。")
        return 0
    except KeyboardInterrupt:
        print("用户取消。")
        return 1
    except Exception as exc:
        print(f"download_tts 失败: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
