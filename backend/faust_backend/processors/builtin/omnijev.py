"""OmniJev Processor：全模态决策模型跑在独立子进程里。

一次 ``invoke`` = 一次前向、0 个生成 token：给定画面（一张/多张静图或一段视频）+ 一组类型化问题，
返回每个问题的校准概率、选项/层级、"都不是"的 abstain 概率与置信度。上游是
https://github.com/tinnel123666888/OmniJev（vendored 于 ``faust_backend/vendor/mso``）。

setup 阶段：下载权重 → 量化 → 缓存量化产物（耗时都在这里，日志走 ``ctx.log``）。
start 阶段：只从本地加载，绝不联网。

config（``manager.require("OMNIJEV", requirer=..., config={...})``）::

    size        "4B"(默认) | "2B" | "0.8B"
    quant       "4bit"(默认) | "8bit" | "none"
    max_pixels  单图视觉 token 上限，默认 768*28*28（上游默认值）
    state_cache 图像状态缓存条数，默认 4（同一画面的多个问题集复用，省一次编码）

invoke 入参 / 出参::

    {"frames": [np.uint8[H,W,3|4] | "图片路径", ...],      # 1 张 = 单图；多张 = 编号面板
     "video": {"path": "clip.mp4"},                      # 可选，与单图互斥（需要 ffmpeg/ffprobe）
     "questions": {"qid": {"type": "noul"|"choice"|"score", ...}}}
  → {"answers": {...}, "frames": [...], "tokens": int, "latency_s": float}

题目形状（上游契约，见 vendor/mso/infer.py 顶部注释）::

    noul   {"type":"noul","instructions":"画面里在下雨。"}
           → {"noul": P(yes)}
    choice {"type":"choice","instructions":"下一步点哪里？",
            "criteria":{"按钮A":null,"按钮B":null}}        # 或 "options":[{"text":...}] / [{"region":{"box":[x1,y1,x2,y2]}}]
           → {"choice": key, "probabilities": {...}, "abstain": P(都不是), "valid": bool, "confidence": c}
    score  {"type":"score","instructions":"任务完成到哪一步？","levels":["刚开始","过半","快好了"]}
           → {"score": level, "probabilities": {...}, "confidence": c}
"""

from __future__ import annotations

import gc
import hashlib
import time
from pathlib import Path
from typing import Any

import numpy as np

import faust_backend.config_loader as conf
from faust_backend import download_omnijev, omnijev_runtime

from ..base import Processor, ProcessorContext
from ..registry import processor

#: 模型资产根目录（对齐 OCR：用户数据目录下的 models/，不污染仓库）
ROOT_DIR = Path(conf.MODEL_ROOT) / "omnijev"

DEFAULT_MAX_PIXELS = 768 * 28 * 28
DEFAULT_STATE_CACHE = 4


def normalize_config(config: dict[str, Any]) -> dict[str, Any]:
    """归一 Processor config；非法值立即报错（不静默取默认）。"""
    raw = dict(config or {})
    size = omnijev_runtime.normalize_size(raw.get("size"))
    quant = omnijev_runtime.normalize_quant(raw.get("quant"))
    max_pixels = int(raw["max_pixels"]) if raw.get("max_pixels") is not None else DEFAULT_MAX_PIXELS
    state_cache = int(raw["state_cache"]) if raw.get("state_cache") is not None else DEFAULT_STATE_CACHE
    if max_pixels < 28 * 28:
        raise ValueError(f"max_pixels 过小: {max_pixels}")
    if state_cache < 0:
        raise ValueError(f"state_cache 不能为负: {state_cache}")
    extra = sorted(set(raw) - {"size", "quant", "max_pixels", "state_cache"})
    if extra:
        raise ValueError(f"不支持的 config 字段: {', '.join(extra)}")
    return {"size": size, "quant": quant, "max_pixels": max_pixels, "state_cache": state_cache}


@processor("OMNIJEV")
class OmnijevProcessor(Processor):
    """图像/视频 + 类型化问题 → 校准概率（一次前向）。"""

    NAME = "OMNIJEV"
    #: 资产目录=MODEL_ROOT/omnijev（marker 也放这里）
    DATA_DIR = ROOT_DIR
    SETUP_VERSION = "1"
    #: 9GB 级下载 + 量化，给足（超时不杀 worker，但会中断本次 setup）
    SETUP_TIMEOUT = 21600.0
    START_TIMEOUT = 900.0
    STOP_TIMEOUT = 30.0
    INVOKE_TIMEOUT = 300.0

    def __init__(self) -> None:
        self._m: Any = None
        self._cfg: dict[str, Any] = {}
        self._dirs: dict[str, Path] = {}

    # ── 生命周期 ──────────────────────────────────────────

    def setup_fingerprint(self, config: dict[str, Any]) -> str:
        """尺寸/量化档位会换权重与量化产物，必须重跑 setup；max_pixels/state_cache 不影响。"""
        cfg = normalize_config(config)
        revision = omnijev_runtime.SIZES[cfg["size"]]["revision"]
        return f"{self.SETUP_VERSION}:{cfg['size']}:{cfg['quant']}:{revision}"

    def setup(self, ctx: ProcessorContext) -> None:
        """下载基座/adapter → 量化 → 缓存量化产物。"""
        cfg = normalize_config(ctx.config)
        root = Path(ctx.data_dir)
        ctx.log(f"setup 开始: size={cfg['size']} quant={cfg['quant']} max_pixels={cfg['max_pixels']} "
                f"state_cache={cfg['state_cache']}")
        ctx.log(f"模型资产目录: {root}（已有 {download_omnijev.human(download_omnijev.dir_bytes(root))}）")
        missing = omnijev_runtime.missing_video_binaries()
        if missing:
            ctx.log(f"PATH 上缺少 {'/'.join(missing)}：视频输入不可用（静图不受影响）", level="WARNING")

        started = time.monotonic()
        assets = download_omnijev.ensure_assets(root, cfg["size"], cfg["quant"], ctx.log)
        ctx.log(f"setup 完成: 用时 {time.monotonic() - started:.0f}s，量化产物 {assets['model']}")

    def start(self, ctx: ProcessorContext) -> None:
        """从本地加载模型（含量化权重还原），记录设备/标定参数。"""
        cfg = normalize_config(ctx.config)
        self._cfg = cfg
        self._dirs = self._resolve_dirs(ctx)
        ctx.log(f"start: quant={cfg['quant']} base={self._dirs['model']} ckpt={self._dirs['ckpt']}")
        for key in ("model", "ckpt"):
            if not self._dirs[key].is_dir():
                raise RuntimeError(f"{key} 目录不存在: {self._dirs[key]}（setup 未成功完成）")

        started = time.monotonic()
        self._m = omnijev_runtime.load_system_one(
            ckpt=self._dirs["ckpt"],
            base=self._dirs["model"],
            quant=cfg["quant"],
            max_pixels=cfg["max_pixels"],
            panels_dir=self._dirs["panels"],
            state_cache=cfg["state_cache"],
            log=ctx.log,
        )
        ctx.log(f"模型加载完成: 用时 {time.monotonic() - started:.1f}s，设备={self._m.dev} dtype={self._m.dtype} "
                f"branch={self._m.branch} 标定温度={self._m.temps} noul偏置={self._m.biases}")
        if self._m.dev.type == "cuda":
            import torch  # pyright: ignore[reportMissingImports]  # 由 setup-runtime.bat 按显卡变体安装，见 download_omnijev.py

            ctx.log(f"显存: allocated={torch.cuda.memory_allocated() / 2**30:.2f}GB "
                    f"reserved={torch.cuda.memory_reserved() / 2**30:.2f}GB "
                    f"device={torch.cuda.get_device_name(0)}")
        else:
            ctx.log("未使用 GPU（cuda 不可用）：推理会明显变慢", level="WARNING")
        if omnijev_runtime.missing_video_binaries():
            ctx.log("ffmpeg/ffprobe 缺失：带 video 的调用会被拒绝", level="WARNING")

    def invoke(self, ctx: ProcessorContext, data: Any) -> dict[str, Any]:
        """解析画面 → 一次前向 → 逐问题回填答案/概率/耗时。"""
        if self._m is None:
            raise RuntimeError("OmniJev 模型未加载")
        payload = data if isinstance(data, dict) else {}
        questions = payload.get("questions")
        if not isinstance(questions, dict) or not questions:
            raise ValueError("questions 必须是非空 dict: {qid: {type, instructions, ...}}")
        for qid, question in questions.items():
            self._check_question(str(qid), question)

        video = payload.get("video")
        images, video_state = self._resolve_state(ctx, payload, video)
        questions = self._prepare_questions(questions)
        ctx.log(f"invoke: 画面={images}{' video=' + str(video_state['video']) if video_state else ''} "
                f"问题 {len(questions)} 个: {', '.join(questions)}")

        started = time.monotonic()
        answers = self._m.system_one({**video_state, "images": images} if video_state else {"images": images},
                                     questions)
        elapsed = time.monotonic() - started
        tokens = int(getattr(self._m, "last_input_tokens", 0) or 0)
        for qid, answer in answers.items():
            ctx.log(f"  {qid} → {self._summarize(questions[qid], answer)}")
        ctx.log(f"invoke 完成: {len(questions)} 问 / {tokens} tokens / {elapsed:.2f}s")
        return {"answers": answers, "frames": images, "video": video_state, "tokens": tokens,
                "latency_s": round(elapsed, 4)}

    def stop(self, ctx: ProcessorContext) -> None:
        """释放模型与显存。"""
        had_model = self._m is not None
        self._m = None
        gc.collect()
        try:
            import torch  # pyright: ignore[reportMissingImports]  # 由 setup-runtime.bat 按显卡变体安装

            if torch.cuda.is_available():
                free_before = torch.cuda.memory_reserved() / 2**30
                torch.cuda.empty_cache()
                ctx.log(f"已释放模型: reserved {free_before:.2f}GB → {torch.cuda.memory_reserved() / 2**30:.2f}GB")
            else:
                ctx.log("已释放模型（无 GPU）")
        except Exception as exc:  # noqa: BLE001 - 释放失败不该阻断退出
            ctx.log(f"torch.cuda.empty_cache 失败: {exc}", level="WARNING")
        if not had_model:
            ctx.log("stop: 模型本就未加载")

    # ── 内部 ──────────────────────────────────────────────

    def _resolve_dirs(self, ctx: ProcessorContext) -> dict[str, Path]:
        """资产路径（与 setup 的布局一致）。"""
        root = Path(ctx.data_dir)
        cfg = normalize_config(ctx.config)
        model = root / cfg["size"] / "base" if cfg["quant"] == "none" else root / cfg["size"] / f"base-{cfg['quant']}"
        return {
            "root": root,
            "model": model,
            "ckpt": root / cfg["size"] / "ckpt",
            "frames": root / "frames",
            "panels": root / "panels",
        }

    @staticmethod
    def _check_question(qid: str, question: Any) -> None:
        if not isinstance(question, dict):
            raise ValueError(f"问题 {qid} 必须是 dict")
        qtype = question.get("type")
        if qtype not in ("noul", "choice", "score"):
            raise ValueError(f"问题 {qid} 的 type 非法: {qtype!r}（可选 noul/choice/score）")
        if not str(question.get("instructions") or "").strip():
            raise ValueError(f"问题 {qid} 缺少 instructions")
        if qtype == "choice" and not (question.get("criteria") or question.get("options")):
            raise ValueError(f"问题 {qid} 需要 criteria（映射）或 options（列表）")
        if qtype == "score" and not (question.get("levels") or question.get("criteria")):
            raise ValueError(f"问题 {qid} 需要 levels 或 criteria")

    @staticmethod
    def _prepare_questions(questions: dict[str, Any]) -> dict[str, Any]:
        """选项列表补 key（上游对 list 形式要求 key 或 text；region 选项必须带 key）。"""
        prepared: dict[str, Any] = {}
        for qid, question in questions.items():
            q = dict(question)
            options = q.get("options")
            if isinstance(options, list):
                fixed = []
                for index, option in enumerate(options):
                    item = dict(option) if isinstance(option, dict) else {"text": str(option)}
                    if not (item.get("key") or item.get("text")):
                        item["key"] = f"option_{index}"
                    fixed.append(item)
                q["options"] = fixed
            prepared[str(qid)] = q
        return prepared

    def _resolve_state(self, ctx: ProcessorContext, payload: dict[str, Any],
                       video: Any) -> tuple[list[str], dict[str, Any]]:
        """把 frames/video 归一成 MSO1 需要的本地文件路径（模型按路径读图）。"""
        if video:
            path = video.get("path") if isinstance(video, dict) else video
            if not isinstance(path, (str, Path)):
                raise ValueError('video 必须是 {"path": "..."} 或路径字符串')
            clip = Path(path)
            if not clip.is_file():
                raise FileNotFoundError(f"video 不存在: {clip}")
            missing = omnijev_runtime.missing_video_binaries()
            if missing:
                raise RuntimeError(f"视频输入需要 {'/'.join(missing)} 在 PATH 上（当前缺失）")
            mosaic = self._dirs["frames"] / f"{self._clip_digest(clip)}.mosaic.jpg"
            self._dirs["frames"].mkdir(parents=True, exist_ok=True)
            ctx.log(f"视频马赛克: {clip.name} → {mosaic.name}（16 帧 @384px，需要 ffmpeg 抽帧）")
            state = omnijev_runtime.video_state(clip, mosaic)
            ctx.log(f"视频状态: {state['video']}")
            return [str(state["images"][0])], {"video": state["video"]}

        frames = payload.get("frames")
        if not isinstance(frames, list) or not frames:
            raise ValueError("frames 必须是非空列表（多张静图会被拼成编号面板；视频请用 video 字段）")
        images: list[str] = []
        for index, item in enumerate(frames):
            if isinstance(item, (str, Path)):
                path = Path(item)
                if not path.is_file():
                    raise FileNotFoundError(f"frames[{index}] 不存在: {path}")
                images.append(str(path))
                continue
            images.append(self._dump_frame(ctx, item, index))
        return images, {}

    @staticmethod
    def _clip_digest(clip: Path) -> str:
        """按 (路径, 大小, mtime) 命名马赛克：同一段视频复用，改动后自动重算。"""
        stat = clip.stat()
        return hashlib.sha1(f"{clip.resolve()}|{stat.st_size}|{stat.st_mtime_ns}".encode()).hexdigest()[:20]

    def _dump_frame(self, ctx: ProcessorContext, frame: Any, index: int) -> str:
        """把主进程传来的图像数组落盘（内容寻址，重复帧不重写以免破坏状态缓存）。"""
        array = np.asarray(frame)
        if array.dtype != np.uint8 or array.ndim != 3 or array.shape[2] not in (3, 4):
            raise ValueError(f"frames[{index}] 期望 uint8[H,W,3|4]，收到 {array.dtype}{array.shape}")
        digest = hashlib.sha1(np.ascontiguousarray(array).tobytes()).hexdigest()[:20]
        path = self._dirs["frames"] / f"{digest}.jpg"
        if path.is_file():
            return str(path)
        from PIL import Image

        self._dirs["frames"].mkdir(parents=True, exist_ok=True)
        tmp = path.with_suffix(".part.jpg")
        Image.fromarray(array[:, :, :3]).save(tmp, format="JPEG", quality=92)
        tmp.replace(path)
        return str(path)

    @staticmethod
    def _summarize(question: dict[str, Any], answer: dict[str, Any]) -> str:
        """一行摘要：结论 + 概率 + 置信度（写进 Processor 日志）。"""
        if question["type"] == "noul":
            return f"noul P(yes)={answer.get('noul')}"
        picked = answer.get("choice") or answer.get("score")
        probs = answer.get("probabilities") or {}
        extra = f" abstain={answer['abstain']}" if "abstain" in answer else ""
        return (f"{question['type']} {picked} p={probs.get(picked, 0.0)} "
                f"conf={answer.get('confidence')}{extra}")
