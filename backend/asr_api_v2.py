print("FaustBot Backend ASR Service\nBooting...")
from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware

import torch
import numpy as np
import os
import sys
import re

from datetime import datetime
from faust_backend.logger import get_logger
import faust_backend.config_loader as conf

os.chdir(os.path.dirname(os.path.abspath(__file__)))

log = get_logger("faust.asr")

# 启动时根据配置决定使用哪个 ASR 引擎：funasr 走 FunASR，其余（默认 whisper）走 OpenAI Whisper。
ENGINE = "funasr" if str(conf.ASR_MODE or "whisper").strip().lower() == "funasr" else "whisper"

model = None


def _load_funasr():
    from funasr import AutoModel

    device = "cuda:0" if torch.cuda.is_available() else "cpu"
    log.info("加载 FunASR 模型 (device=%s)...", device)
    return AutoModel(
        model="FunAudioLLM/Fun-ASR-Nano-2512",
        trust_remote_code=True,
        remote_code="./model.py",
        vad_model="fsmn-vad",
        vad_kwargs={"max_single_segment_time": 30000},
        device=device,
        disable_update=True,
    )


def _load_whisper():
    import whisper

    device = "cuda" if torch.cuda.is_available() else "cpu"
    model_name = str(conf.WHISPER_MODEL or "small").strip()
    log.info("加载 Whisper 模型 '%s' (device=%s)...", model_name, device)
    return whisper.load_model(model_name, device=device)


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("正在加载 ASR 模型 (引擎=%s)...", ENGINE)
    global model
    if ENGINE == "funasr":
        model = _load_funasr()
    else:
        model = _load_whisper()
    yield


app = FastAPI(lifespan=lifespan)

# 添加 CORS 中间件
app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


def _read_mono_float32(audio_bytes: bytes):
    import io
    import soundfile as sf

    audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
    audio_data = np.asarray(audio_data)
    if audio_data.ndim > 1:
        audio_data = np.mean(audio_data, axis=1)
    return audio_data.astype("float32"), int(sample_rate)


def _transcribe_funasr(audio_data: np.ndarray) -> str:
    with torch.no_grad():
        asr_result = model.generate(input=audio_data, dtype="float32")
    return str(asr_result[0]["text"])


def _transcribe_whisper(audio_data: np.ndarray, sample_rate: int) -> str:
    # Whisper 期望 16kHz 单声道 float32 数组
    if sample_rate != 16000:
        import librosa

        audio_data = librosa.resample(
            audio_data, orig_sr=sample_rate, target_sr=16000
        ).astype("float32")
    language = str(conf.WHISPER_LANGUAGE or "").strip() or None
    initial_prompt = str(conf.WHISPER_INITIAL_PROMPT or "").strip() or None
    result = model.transcribe(
        audio_data,
        language=language,
        initial_prompt=initial_prompt,
        fp16=torch.cuda.is_available(),
    )
    return str(result.get("text") or "").strip()


@app.post("/v1/upload_audio")
async def upload_audio(file: UploadFile = File(...)):
    try:
        audio_bytes = await file.read()
        audio_data, sample_rate = _read_mono_float32(audio_bytes)
        log.info("音频数据形状: %s, 采样率: %s, 引擎: %s", audio_data.shape, sample_rate, ENGINE)

        if ENGINE == "funasr":
            text = _transcribe_funasr(audio_data)
        else:
            text = _transcribe_whisper(audio_data, sample_rate)

        return {
            "status": "success",
            "filename": file.filename or "uploaded_audio",
            "text": text,
        }

    except Exception as e:
        log.error(f"处理音频时出错: {str(e)}")
        return {"status": "error", "message": str(e)}


if __name__ == "__main__":
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=1000)
