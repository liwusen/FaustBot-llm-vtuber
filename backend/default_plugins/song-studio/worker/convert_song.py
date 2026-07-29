"""歌曲转换 worker：在 sva-runtime 独立 Python 中运行。

流程：人声分离(audio-separator BS-Roformer) → Seed-VC f0 歌声转换 → 混音导出。
进度协议：向 stdout 打 JSON 行 {"stage": str, "percent": float, "message": str}。
父进程（song-studio 插件）逐行解析。任何失败都立刻报错退出（exit code 1）。
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import subprocess
import sys
import tempfile
import time
from pathlib import Path

SEPARATOR_MODEL = "model_bs_roformer_ep_317_sdr_12.9755.ckpt"


def emit(stage: str, percent: float, message: str = "") -> None:
    print(json.dumps({"stage": stage, "percent": percent, "message": message},
                     ensure_ascii=False), flush=True)


def separate(source: Path, work_dir: Path, model_dir: Path) -> tuple[Path, Path]:
    emit("separate", 0, "加载分离模型 (BS-Roformer)...")
    from audio_separator.separator import Separator

    separator = Separator(
        output_dir=str(work_dir),
        model_file_dir=str(model_dir),
        output_format="wav",
    )
    separator.load_model(model_filename=SEPARATOR_MODEL)
    emit("separate", 30, "分离人声/伴奏...")
    output_files = separator.separate(str(source))

    vocals = instrumental = None
    for name in output_files:
        path = work_dir / name
        lower = name.lower()
        if "(vocals)" in lower:
            vocals = path
        elif "(instrumental)" in lower:
            instrumental = path
    if vocals is None or instrumental is None:
        raise RuntimeError(f"分离输出缺少 Vocals/Instrumental: {output_files}")
    emit("separate", 100, "分离完成")
    return vocals, instrumental


def convert_vocals(vocals: Path, reference: Path, work_dir: Path, seedvc: Path,
                   diffusion_steps: int, semi_tone_shift: int, auto_f0: bool) -> Path:
    emit("convert", 0, "启动 Seed-VC 推理...")
    out_dir = work_dir / "vc-out"
    out_dir.mkdir(parents=True, exist_ok=True)
    env = dict(os.environ)
    env.setdefault("HF_ENDPOINT", "https://hf-mirror.com")
    # 子进程 stdout 被 PIPE 时在 Windows 默认用 gbk 编码，进度条等字符会触发
    # UnicodeEncodeError，强制 utf-8 以匹配下方 text/encoding="utf-8" 的读取
    env["PYTHONIOENCODING"] = "utf-8"
    cmd = [
        sys.executable, str(seedvc / "inference.py"),
        "--source", str(vocals),
        "--target", str(reference),
        "--output", str(out_dir),
        "--f0-condition", "True",
        "--auto-f0-adjust", "True" if auto_f0 else "False",
        "--semi-tone-shift", str(semi_tone_shift),
        "--diffusion-steps", str(diffusion_steps),
    ]
    proc = subprocess.Popen(
        cmd, cwd=str(seedvc), env=env,
        stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        text=True, encoding="utf-8", errors="replace")
    assert proc.stdout is not None
    log_tail: list[str] = []
    for line in proc.stdout:
        line = line.strip()
        if not line:
            continue
        log_tail.append(line)
        log_tail = log_tail[-40:]
        emit("convert", -1, line)
    code = proc.wait()
    if code != 0:
        raise RuntimeError("Seed-VC 推理失败 (exit %d):\n%s" % (code, "\n".join(log_tail[-10:])))

    outputs = sorted(out_dir.glob("vc_*.wav"))
    if not outputs:
        raise RuntimeError(f"Seed-VC 未产出结果文件: {out_dir}")
    emit("convert", 100, "歌声转换完成")
    return outputs[-1]


def mix(converted_vocals: Path, instrumental: Path, output: Path, vocal_gain_db: float) -> None:
    emit("mix", 0, "混音...")
    import numpy as np
    import soundfile as sf
    import librosa

    voc, voc_sr = sf.read(str(converted_vocals), dtype="float32", always_2d=True)
    inst, inst_sr = sf.read(str(instrumental), dtype="float32", always_2d=True)

    target_sr = inst_sr
    if voc_sr != target_sr:
        voc = librosa.resample(voc.T, orig_sr=voc_sr, target_sr=target_sr).T

    if voc.shape[1] == 1 and inst.shape[1] == 2:
        voc = np.repeat(voc, 2, axis=1)
    elif voc.shape[1] == 2 and inst.shape[1] == 1:
        inst = np.repeat(inst, 2, axis=1)

    length = max(voc.shape[0], inst.shape[0])
    voc = np.pad(voc, ((0, length - voc.shape[0]), (0, 0)))
    inst = np.pad(inst, ((0, length - inst.shape[0]), (0, 0)))

    voc_gain = 10 ** (vocal_gain_db / 20.0)
    mixed = inst + voc * voc_gain
    peak = float(np.max(np.abs(mixed)))
    if peak > 0.98:
        mixed = mixed * (0.98 / peak)

    output.parent.mkdir(parents=True, exist_ok=True)
    sf.write(str(output), mixed, target_sr, subtype="PCM_16")
    emit("mix", 100, "混音完成")


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--source", required=True)
    parser.add_argument("--reference", required=True)
    parser.add_argument("--output", required=True, help="final.wav 输出路径")
    parser.add_argument("--seedvc-dir", required=True)
    parser.add_argument("--model-dir", required=True, help="分离模型缓存目录")
    parser.add_argument("--diffusion-steps", type=int, default=30)
    parser.add_argument("--semi-tone-shift", type=int, default=0)
    parser.add_argument("--auto-f0", action="store_true")
    parser.add_argument("--vocal-gain-db", type=float, default=0.0)
    args = parser.parse_args()

    source = Path(args.source)
    reference = Path(args.reference)
    if not source.exists():
        raise FileNotFoundError(f"源歌曲不存在: {source}")
    if not reference.exists():
        raise FileNotFoundError(f"参考音频不存在: {reference}")

    started = time.time()
    work_dir = Path(tempfile.mkdtemp(prefix="sva-"))
    try:
        vocals, instrumental = separate(source, work_dir, Path(args.model_dir))
        converted = convert_vocals(
            vocals, reference, work_dir, Path(args.seedvc_dir),
            args.diffusion_steps, args.semi_tone_shift, args.auto_f0)
        mix(converted, instrumental, Path(args.output), args.vocal_gain_db)
    finally:
        shutil.rmtree(work_dir, ignore_errors=True)

    emit("done", 100, f"完成，耗时 {time.time() - started:.0f}s")
    return 0


if __name__ == "__main__":
    try:
        sys.exit(main())
    except Exception as exc:
        emit("error", -1, f"{type(exc).__name__}: {exc}")
        sys.exit(1)
