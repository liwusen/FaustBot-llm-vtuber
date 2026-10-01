"""OmniJev 模型资产：从 HuggingFace 镜像下载基座/adapter，并把量化后的基座缓存到本地。

上游权重只在 HuggingFace 上（ModelScope 没有 ``tinnel123/OmniJev*``），而 ``huggingface.co``
在部分地区不可直接访问：未显式设置 ``HF_ENDPOINT`` 时兜底到 ``hf-mirror.com``（与 song-studio
插件的做法一致）。

setup 阶段的完整流程 = 下载 → 量化 → 缓存量化产物；``start`` 阶段只从本地加载，不再量化、
不再联网。量化档位为 ``none`` 时跳过量化，直接用上游 bf16 权重。
"""

from __future__ import annotations

import gc
import json
import os
import shutil
import threading
import time
from pathlib import Path
from typing import Callable

from tqdm.auto import tqdm as _tqdm_auto

from faust_backend.omnijev_runtime import SIZES, quant_config

LogFn = Callable[[str], None]

#: ``huggingface.co`` 不可达时的兜底镜像
HF_MIRROR = "https://hf-mirror.com"
#: 下载并发（1 线程 1 文件）
MAX_WORKERS = 8
#: 单文件元数据请求超时
ETAG_TIMEOUT = 30.0
#: 下载进度日志间隔（秒）
PROGRESS_INTERVAL_S = 10.0

#: 基座必备文件
_BASE_REQUIRED = ("config.json",)
#: adapter（ckpt）必备文件：MSO1 会读其中每一个（ord.pt 视 head_meta.ordinal 另判）
_CKPT_REQUIRED = (
    "adapter_config.json",
    "adapter_model.safetensors",
    "head.pt",
    "head_meta.json",
    "new_tok_emb.pt",
    "processor_config.json",
    "tokenizer.json",
    "tokenizer_config.json",
)


def human(size: float) -> str:
    """字节数 → 人类可读。"""
    value = float(size)
    for unit in ("B", "KB", "MB", "GB", "TB"):
        if value < 1024 or unit == "TB":
            return f"{value:.1f}{unit}" if unit != "B" else f"{int(value)}B"
        value /= 1024
    return f"{value:.1f}TB"


def dir_bytes(path: str | Path) -> int:
    """目录占用（含子目录，跳过不存在的路径）。"""
    root = Path(path)
    if not root.is_dir():
        return 0
    total = 0
    for item in root.rglob("*"):
        if item.is_file():
            try:
                total += item.stat().st_size
            except OSError:
                pass
    return total


def ensure_endpoint(log: LogFn) -> str:
    """确保 ``HF_ENDPOINT`` 指向可用端点（未设置则用镜像），返回实际端点。"""
    endpoint = os.environ.get("HF_ENDPOINT")
    if not endpoint:
        os.environ["HF_ENDPOINT"] = HF_MIRROR
        endpoint = HF_MIRROR
        log(f"未设置 HF_ENDPOINT，使用镜像 {HF_MIRROR}")
    else:
        log(f"使用已有 HF_ENDPOINT: {endpoint}")
    return endpoint


# ── 进度日志 ────────────────────────────────────────────────


def _quiet_http_logs(log: LogFn) -> None:
    """压掉 HF 客户端的逐请求日志。

    huggingface_hub 的 httpx INFO 会把每一次 HEAD/GET 都经子进程 stdout 灌进 Processor 日志，
    几十 MB 的文本会挤掉真正有用的行。传输细节对我们没价值，进度由 _DirProgress 从磁盘读。
    """
    os.environ.setdefault("HF_HUB_VERBOSITY", "error")
    import logging

    for name in ("httpx", "httpcore", "huggingface_hub", "urllib3", "filelock"):
        logging.getLogger(name).setLevel(logging.WARNING)
    log("已把 httpx/huggingface_hub 日志压到 WARNING（避免逐请求日志淹没 Processor 日志）")


def _log_tqdm(log: LogFn):
    """屏蔽 huggingface_hub 的进度条输出（进度见 _DirProgress，别让进度条刷进子进程 stdout）。"""

    class _NoBarTqdm(_tqdm_auto):
        def __init__(self, *args, **kwargs):
            kwargs["disable"] = True
            super().__init__(*args, **kwargs)

    return _NoBarTqdm


class _DirProgress:
    """按本地目录占用报下载进度。

    走 Xet/CAS 传输时 huggingface_hub 的 tqdm 聚合计数是 0（字节不经它手），所以进度只能从
    磁盘读数取：每 ``every`` 秒报一次 本地/总量。
    """

    def __init__(self, directory: Path, total: int, log: LogFn, every: float = PROGRESS_INTERVAL_S) -> None:
        self._dir = directory
        self._total = int(total)
        self._log = log
        self._every = every
        self._stop = threading.Event()
        self._thread: threading.Thread | None = None

    def start(self) -> None:
        self._thread = threading.Thread(target=self._run, name="omnijev-download-progress", daemon=True)
        self._thread.start()

    def stop(self) -> None:
        self._stop.set()
        if self._thread is not None:
            self._thread.join(timeout=2.0)
            self._thread = None

    def _run(self) -> None:
        while not self._stop.wait(self._every):
            local = dir_bytes(self._dir)
            pct = 100.0 * local / self._total if self._total else 0.0
            self._log(f"下载中 {human(local)}/{human(self._total)} ({pct:.0f}%)")


# ── 完整性判定 ──────────────────────────────────────────────


def _weights_present(directory: Path) -> bool:
    """分片权重是否齐全（优先用 index.json 的 weight_map 校验）。"""
    index = directory / "model.safetensors.index.json"
    if index.is_file():
        try:
            weight_map = json.loads(index.read_text(encoding="utf-8"))["weight_map"]
        except Exception:  # noqa: BLE001 - 索引损坏按"不完整"处理，走重新下载
            return False
        names = set(weight_map.values())
        if not names:
            return False
        return all((directory / name).is_file() for name in names)
    return any(p.is_file() and p.stat().st_size > 0 for p in directory.glob("*.safetensors"))


def missing_files(directory: Path, required: tuple[str, ...]) -> list[str]:
    """``required`` 中缺失（或为空）的文件。"""
    return [name for name in required if not (directory / name).is_file()]


def _quantized_complete(directory: Path) -> bool:
    """量化产物是否可用：config 里带 quantization_config 且分片齐全。"""
    config = directory / "config.json"
    if not config.is_file() or not _weights_present(directory):
        return False
    try:
        return "quantization_config" in json.loads(config.read_text(encoding="utf-8"))
    except Exception:  # noqa: BLE001
        return False


# ── 下载 ────────────────────────────────────────────────────


def describe_plan(repo: str, revision: str | None, local_dir: Path, log: LogFn) -> int:
    """下载前把清单写进日志并返回仓库总字节数（失败只告警，不阻断下载）。"""
    try:
        from huggingface_hub import HfApi

        info = HfApi().model_info(repo_id=repo, revision=revision, files_metadata=True)
        siblings = [s for s in (info.siblings or []) if s.size]
        total = sum(int(s.size) for s in siblings)
        todo = [(s.rfilename, int(s.size)) for s in siblings if not (local_dir / s.rfilename).is_file()]
        log(f"{repo}@{revision or 'main'} 清单: {len(siblings)} 个文件 / {human(total)}，"
            f"本地已有 {len(siblings) - len(todo)} 个，待下载 {human(sum(size for _, size in todo))}")
        for name, size in sorted(todo, key=lambda x: -x[1])[:8]:
            log(f"  待下载 {name} ({human(size)})")
        return total
    except Exception as exc:  # noqa: BLE001 - 清单只是日志，拿不到照样下载
        log(f"读取清单失败（不影响下载）: {type(exc).__name__}: {exc}", level="WARNING")
        return 0


def ensure_snapshot(repo: str, revision: str | None, local_dir: str | Path, log: LogFn, label: str,
                    complete: Callable[[Path], bool]) -> Path:
    """确保 ``repo`` 的完整快照在 ``local_dir``；已完整则完全不联网。"""
    target = Path(local_dir)
    if complete(target):
        log(f"{label} 已就绪，跳过下载: {target}（{human(dir_bytes(target))}）")
        return target

    ensure_endpoint(log)
    _quiet_http_logs(log)
    log(f"开始下载{label}: {repo}@{revision or 'main'} → {target}")
    total = describe_plan(repo, revision, target, log)
    progress = _DirProgress(target, total, log)
    progress.start()
    started = time.monotonic()
    try:
        from huggingface_hub import snapshot_download

        snapshot_download(
            repo_id=repo,
            revision=revision,
            local_dir=str(target),
            max_workers=MAX_WORKERS,
            etag_timeout=ETAG_TIMEOUT,
            tqdm_class=_log_tqdm(log),
        )
    except Exception as exc:  # noqa: BLE001 - 失败必须暴露（项目约定：不静默降级）
        raise RuntimeError(f"{label} 下载失败: {type(exc).__name__}: {exc}") from exc
    finally:
        progress.stop()

    used = time.monotonic() - started
    log(f"{label} 下载完成: {human(dir_bytes(target))}，用时 {used:.0f}s"
        f"（{human(dir_bytes(target) / max(used, 1e-6))}/s）")
    if not complete(target):
        raise RuntimeError(f"{label} 下载后仍不完整: {target}")
    return target


def ensure_base(root: Path, size: str, log: LogFn) -> Path:
    """下载 Qwen3.5 基座。"""
    info = SIZES[size]
    return ensure_snapshot(info["base"], None, root / size / "base", log, f"基座 {info['base']}",
                           lambda d: _weights_present(d) and not missing_files(d, _BASE_REQUIRED))


def ensure_ckpt(root: Path, size: str, log: LogFn) -> Path:
    """下载 OmniJev adapter（含 head/ord/新增 token embedding）。"""
    info = SIZES[size]

    def complete(d: Path) -> bool:
        required = list(_CKPT_REQUIRED)
        meta = d / "head_meta.json"
        if meta.is_file():
            try:
                # ordinal 头只有 head_meta 声明要用时才必须存在（MSO1 会按同一条件加载 ord.pt）
                if json.loads(meta.read_text(encoding="utf-8")).get("ordinal"):
                    required.append("ord.pt")
            except Exception:  # noqa: BLE001 - head_meta 损坏视为不完整，重新下载
                return False
        if missing_files(d, tuple(required)):
            return False
        for shard in d.glob("*.safetensors"):
            if shard.stat().st_size == 0:
                return False
        return True

    return ensure_snapshot(info["ckpt"], info["revision"], root / size / "ckpt", log,
                           f"adapter {info['ckpt']}@{info['revision']}", complete)


# ── 量化 ────────────────────────────────────────────────────


def ensure_quantized_base(base_dir: Path, out_dir: Path, kind: str, log: LogFn) -> Path:
    """把 bf16 基座量化后落盘缓存；已缓存则直接复用。

    产物是 bitsandbytes 序列化权重（含 ``quantization_config``），``start`` 阶段按同一档位加载即
    可直接还原，省掉每次启动的量化时间。写入走 ``<name>.part`` 再原子改名，半成品不会被当成缓存。
    """
    if _quantized_complete(out_dir):
        log(f"{kind} 量化缓存命中，跳过量化: {out_dir}（{human(dir_bytes(out_dir))}）")
        return out_dir

    log(f"开始量化: {kind} ← {base_dir}（目标 {out_dir}）")
    started = time.monotonic()
    config = quant_config(kind)
    log(f"量化配置: quant_type={config.bnb_4bit_quant_type} compute_dtype={config.bnb_4bit_compute_dtype} "
        f"double_quant={config.bnb_4bit_use_double_quant} skip={config.llm_int8_skip_modules}")

    # torch 由 setup-runtime.bat --torch <variant> 装进 .runtime（不一定在 pyright 分析的
    # venv 里，例如它只装在用户级 site-packages 的机器上），故此处按运行时导入抑制该诊断
    import torch  # pyright: ignore[reportMissingImports]
    from transformers import AutoModelForImageTextToText
    import transformers.utils.logging as tf_logging

    tf_logging.disable_progress_bar()      # "Loading weights"/"Writing model shards" 进度条不进日志

    tmp_dir = out_dir.with_name(out_dir.name + ".part")
    if tmp_dir.exists():
        shutil.rmtree(tmp_dir)
    out_dir.parent.mkdir(parents=True, exist_ok=True)
    model = None
    try:
        model = AutoModelForImageTextToText.from_pretrained(
            str(base_dir), dtype=torch.bfloat16, quantization_config=config)
        used = time.monotonic() - started
        if torch.cuda.is_available():
            log(f"量化加载完成（{used:.0f}s），显存 {torch.cuda.memory_allocated() / 2**30:.2f}GB")
        else:
            log(f"量化加载完成（{used:.0f}s，CPU：量化过程会比较慢）")
        model.save_pretrained(str(tmp_dir), safe_serialization=True)
        if not _quantized_complete(tmp_dir):
            raise RuntimeError(f"量化产物不完整: {tmp_dir}")
        os.replace(tmp_dir, out_dir)
    finally:
        model = None
        gc.collect()
        if torch.cuda.is_available():
            torch.cuda.empty_cache()

    log(f"量化完成: {kind} {human(dir_bytes(out_dir))}，用时 {time.monotonic() - started:.0f}s → {out_dir}")
    return out_dir


def ensure_assets(root: str | Path, size: str, quant: str, log: LogFn) -> dict[str, Path]:
    """setup 的全部模型工作：下载基座/adapter、量化并缓存，返回各路径。"""
    root = Path(root)
    log(f"模型资产目录: {root}（尺寸 {size}，量化 {quant}）")
    base_dir = ensure_base(root, size, log)
    ckpt_dir = ensure_ckpt(root, size, log)
    if quant == "none":
        log("量化档位为 none：直接使用上游 bf16 权重")
        model_dir = base_dir
    else:
        model_dir = ensure_quantized_base(base_dir, root / size / f"base-{quant}", quant, log)
    log(f"模型就绪: base={model_dir} ckpt={ckpt_dir} "
        f"（磁盘合计 {human(dir_bytes(root))}；如需回收空间可删除 {root / size / 'base'}，"
        f"下次 setup 会重新下载）")
    return {"root": root, "base": base_dir, "ckpt": ckpt_dir, "model": model_dir}
