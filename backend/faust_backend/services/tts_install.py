"""GPT-SoVITS 安装发现与 V5 权重解析。

只做磁盘布局判断，不加载 torch / 不启动服务，供组件状态与 TTS 启动复用。
"""

from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

import faust_backend.core.config as conf

# V5 相对安装根的文件路径
V5_MODELS_MODULE = Path("GPT_SoVITS/module/models_v5.py")
V5_TURBO_WEIGHT = Path("GPT_SoVITS/pretrained_models/gsv-v5-pretrained/s2Gv5turbo.pth")
V5_DEV_WEIGHT = Path("GPT_SoVITS/pretrained_models/gsv-v5-pretrained/s2Gv5dev.pth")
V5_GPT_WEIGHT = Path("GPT_SoVITS/pretrained_models/s1v3.ckpt")


def normalize_v5_model(value: str | None) -> str:
    return "v5dev" if str(value or "").strip().lower() == "v5dev" else "v5turbo"


@dataclass(frozen=True)
class TtsInstall:
    """一个可启动的 GPT-SoVITS 安装。"""

    root: Path
    version: str  # "v5" | "v5-incomplete" | "legacy"
    variant: str  # "standard" | "nvidia50"
    available_models: tuple[str, ...] = ()

    @property
    def is_v5(self) -> bool:
        return self.version == "v5"


def _default_tts_hub() -> Path:
    return Path(conf.BACKEND_ROOT) / "tts-hub"


def _candidate_dirs(tts_hub: Path) -> list[Path]:
    """返回含 api.py 的安装目录；兼容整合包多套一层的旧布局。"""
    found: list[Path] = []
    if not tts_hub.is_dir():
        return found
    for child in sorted(tts_hub.iterdir(), key=lambda p: p.name.lower()):
        if not child.is_dir() or child.name.startswith("."):
            continue
        if (child / "api.py").is_file():
            found.append(child)
            continue
        for nested in sorted(child.iterdir(), key=lambda p: p.name.lower()):
            if (
                nested.is_dir()
                and not nested.name.startswith(".")
                and (nested / "api.py").is_file()
            ):
                found.append(nested)
    return found


def _inspect(root: Path) -> TtsInstall:
    available = tuple(
        model
        for model, relative in (("v5turbo", V5_TURBO_WEIGHT), ("v5dev", V5_DEV_WEIGHT))
        if (root / relative).is_file()
    )
    has_v5_code = (root / V5_MODELS_MODULE).is_file()
    if has_v5_code and available:
        version = "v5"
    elif has_v5_code:
        version = "v5-incomplete"
    else:
        version = "legacy"

    variant = "nvidia50" if "nvidia50" in root.name.lower() else "standard"
    return TtsInstall(root=root, version=version, variant=variant, available_models=available)


def _rank(install: TtsInstall) -> tuple[int, float, int, str]:
    try:
        modified = install.root.stat().st_mtime
    except OSError:
        modified = 0.0
    bundle_rank = 1 if install.root.name.lower() == "gpt-sovits-bundle" else 0
    if install.version == "v5":
        return 2, modified, 0, install.root.name.lower()
    if install.version == "v5-incomplete":
        return 1, modified, 0, install.root.name.lower()
    return 0, 0.0, bundle_rank, install.root.name.lower()


def find_tts_install(tts_hub: Path | None = None) -> TtsInstall | None:
    """选择当前应使用的安装：V5 > V5 代码不完整 > 旧版；旧版优先 Bundle。"""
    root = Path(tts_hub) if tts_hub is not None else _default_tts_hub()
    candidates = [_inspect(path) for path in _candidate_dirs(root)]
    if not candidates:
        return None
    return max(candidates, key=_rank)


def v5_weight_paths(install: TtsInstall, model: str | None = None) -> tuple[Path, Path]:
    """返回 (SoVITS 权重, GPT 权重) 的绝对路径。

    model 为 v5dev 且该权重缺失时回退 v5turbo；两者都缺失则显式报错。
    """
    selected = normalize_v5_model(model)
    relative = V5_DEV_WEIGHT if selected == "v5dev" else V5_TURBO_WEIGHT
    sovits = install.root / relative
    if not sovits.is_file() and selected == "v5dev":
        selected = "v5turbo"
        relative = V5_TURBO_WEIGHT
        sovits = install.root / relative
    if not sovits.is_file():
        raise FileNotFoundError(
            f"V5 SoVITS 权重缺失: {sovits}（请重新下载 V5 全量包或执行增量升级）"
        )

    gpt = install.root / V5_GPT_WEIGHT
    if not gpt.is_file():
        raise FileNotFoundError(f"V5 GPT 权重缺失: {gpt}（请重新下载 V5 全量包或执行增量升级）")
    return sovits, gpt


def tts_status(tts_hub: Path | None = None) -> dict[str, object]:
    """组件状态 API 使用的 TTS 安装信息。"""
    install = find_tts_install(tts_hub)
    if install is None:
        return {
            "installed": False,
            "path": None,
            "variant": None,
            "version": None,
            "is_v5": False,
            "available_models": [],
        }

    try:
        path = install.root.relative_to(Path(conf.BACKEND_ROOT)).as_posix()
    except ValueError:
        path = str(install.root)
    return {
        "installed": True,
        "path": path,
        "variant": install.variant,
        "version": install.version,
        "is_v5": install.is_v5,
        "available_models": list(install.available_models),
    }
