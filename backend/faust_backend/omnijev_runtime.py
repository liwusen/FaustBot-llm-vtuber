"""OmniJev 模型身份与 vendored 推理代码的加载入口。

vendor 目录里的 ``mso`` 包内部使用绝对导入（``import mso.records`` 等），因此必须先把 vendor
目录放进 ``sys.path`` 才能 ``import mso``。这里统一处理（进程内幂等），并集中定义"有哪些尺寸、
有哪些量化档位"以及唯一的重依赖入口（torch/transformers 只在这里 import）。
"""

from __future__ import annotations

import os
import shutil
import sys
from pathlib import Path
from typing import Any

#: vendored 上游推理代码所在目录（其下是 ``mso`` 包）
VENDOR_DIR = Path(__file__).resolve().parent / "vendor"

#: 可选模型尺寸 → 上游仓库（ckpt=OmniJev adapter，base=Qwen3.5 基座）
SIZES: dict[str, dict[str, str]] = {
    "0.8B": {"ckpt": "tinnel123/OmniJev-0.8B", "base": "Qwen/Qwen3.5-0.8B", "revision": "v1.1"},
    "2B": {"ckpt": "tinnel123/OmniJev-2B", "base": "Qwen/Qwen3.5-2B", "revision": "v1.1"},
    "4B": {"ckpt": "tinnel123/OmniJev", "base": "Qwen/Qwen3.5-4B", "revision": "v1.1"},
}

#: 量化档位（none = 上游 bf16 路径）
QUANT_KINDS = ("4bit", "8bit", "none")

#: 默认尺寸与档位（4bit 是 8GB 显存跑 4B 的唯一可行档，见 vendor/mso/q4.py 的实测数字）
DEFAULT_SIZE = "4B"
DEFAULT_QUANT = "4bit"


def normalize_size(size: Any) -> str:
    """归一尺寸名；未知尺寸立即报错。"""
    value = str(size or DEFAULT_SIZE).strip()
    if value not in SIZES:
        raise ValueError(f"未知的 OmniJev 尺寸: {value!r}（可选: {', '.join(SIZES)}）")
    return value


def normalize_quant(quant: Any) -> str:
    """归一量化档位。

    未指定（``None``）→ 默认档位；显式空串/``none``/``off``/``false`` → 不量化（走上游 bf16）。
    """
    if quant is None:
        return DEFAULT_QUANT
    value = str(quant).strip().lower()
    if value in ("", "none", "off", "false"):
        return "none"
    if value not in QUANT_KINDS:
        raise ValueError(f"未知的量化档位: {value!r}（可选: {', '.join(QUANT_KINDS)}）")
    return value


def ensure_importable() -> Path:
    """把 vendor 目录插到 ``sys.path`` 首位（幂等），返回该目录。"""
    path = str(VENDOR_DIR)
    if path not in sys.path:
        sys.path.insert(0, path)
    return VENDOR_DIR


def quant_config(kind: str):
    """按档位取 ``BitsAndBytesConfig``（只允许量化档位）。"""
    ensure_importable()
    from mso import q4

    return q4.config(kind)


def missing_video_binaries() -> list[str]:
    """视频输入需要的可执行文件里缺哪些（ffmpeg/ffprobe 都要在 PATH 上）。"""
    return [name for name in ("ffmpeg", "ffprobe") if shutil.which(name) is None]


def video_state(clip: str | Path, mosaic_path: str | Path) -> dict:
    """一段视频 → ``{"images": [马赛克], "video": {...}}``（16 帧 4x4 拼图 + 时间戳记录）。"""
    ensure_importable()
    from mso.video import video_state as _video_state

    return _video_state(str(clip), str(mosaic_path))


def load_system_one(*, ckpt: str | Path, base: str | Path, quant: str, max_pixels: int,
                    panels_dir: str | Path, state_cache: int, log=None) -> Any:
    """加载 MSO1（唯一 import torch/transformers 的地方）。

    ``MSO_PANELS`` / ``MSO_STATE_CACHE`` 必须在下游模块 import 之前设好（``mso.infer`` 在模块级
    读 PANEL_DIR），这里显式设置而不是依赖外部环境。
    """
    kind = normalize_quant(quant)
    panels_dir = Path(panels_dir)
    panels_dir.mkdir(parents=True, exist_ok=True)
    os.environ["MSO_PANELS"] = str(panels_dir)
    os.environ["MSO_STATE_CACHE"] = str(int(state_cache))
    os.environ.setdefault("HF_HUB_OFFLINE", "1")     # start 阶段只读本地文件，绝不偷偷联网
    ensure_importable()

    from mso.infer import MSO1

    if log:
        log(f"构造 MSO1: ckpt={ckpt} base={base} quant={kind} max_pixels={int(max_pixels)} "
            f"state_cache={int(state_cache)}")
    _quiet_transformers()
    return MSO1(str(ckpt), str(base), max_pixels=int(max_pixels),
                quant=None if kind == "none" else kind)


def _quiet_transformers() -> None:
    """关掉 transformers 的 tqdm 进度条（"Loading weights" 之类会经子进程 stdout 灌进日志）。

    只关进度条，不动 warning：像"fla 快路径不可用"这类警告是有用信息。
    """
    import transformers.utils.logging as tf_logging

    tf_logging.disable_progress_bar()
