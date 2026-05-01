from contextlib import asynccontextmanager
from fastapi import FastAPI, UploadFile, File
from fastapi.middleware.cors import CORSMiddleware
from funasr import AutoModel

import torch
import numpy as np
import os
import sys
import re

from datetime import datetime
from faust_backend.logger import get_logger

os.chdir(os.path.dirname(os.path.abspath(__file__)))

log = get_logger("faust.asr")

MODEL_DIR = os.path.join("asr-hub", "model")


@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("正在加载 ASR 模型...")

    asr_model_path = os.path.join(MODEL_DIR, "asr")
    if not os.path.exists(asr_model_path):
        os.makedirs(asr_model_path)

    original_modelscope_cache = os.environ.get('MODELSCOPE_CACHE', '')
    original_funasr_home = os.environ.get('FUNASR_HOME', '')

    os.environ['MODELSCOPE_CACHE'] = asr_model_path
    os.environ['FUNASR_HOME'] = MODEL_DIR

    if _current_asr_mode() == "whisper":
        log.info("正在加载 Whisper ASR 模型...")
        _get_whisper_model()
        log.info("Whisper ASR 模型加载完成")
    else:
        log.info("正在加载 FunASR 模型...")
        model_state["asr_model"] = AutoModel(
            model="iic/speech_paraformer-large-vad-punc_asr_nat-zh-cn-16k-common-vocab8404-pytorch",
            device=device,
            model_type="pytorch",
            dtype="float32"
        )
        log.info("FunASR 模型加载完成")

        log.info("正在加载标点符号模型...")
        model_state["punc_model"] = AutoModel(
            model="iic/punc_ct-transformer_cn-en-common-vocab471067-large",
            model_revision="v2.0.4",
            device=device,
            model_type="pytorch",
            dtype="float32"
        )
    if original_modelscope_cache:
        os.environ['MODELSCOPE_CACHE'] = original_modelscope_cache
    else:
        os.environ.pop('MODELSCOPE_CACHE', None)

    if original_funasr_home:
        os.environ['FUNASR_HOME'] = original_funasr_home
    else:
        os.environ.pop('FUNASR_HOME', None)
    log.info("ASR 模型全部加载完成")

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
if not os.path.exists(MODEL_DIR):
    os.makedirs(MODEL_DIR)

# 设置设备和数据类型
device = "cuda" if torch.cuda.is_available() else "cpu"
torch.set_default_dtype(torch.float32)

# 初始化模型状态
model_state = {
    "asr_model": None,
    "punc_model": None
}

whisper_state = {
    "model": None,
    "loaded_name": None,
}


def _current_asr_mode() -> str:
    try:
        import faust_backend.config_loader as conf
        return str(conf.ASR_MODE or "local").strip().lower()
    except Exception:
        return "local"


def _resolve_whisper_device() -> str:
    try:
        device = str(conf.WHISPER_DEVICE or "auto").strip().lower()
    except Exception:
        device = "auto"
    if device in {"", "auto"}:
        return "cuda" if torch.cuda.is_available() else "cpu"
    return device


def _get_whisper_model():
    import faust_backend.config_loader as conf
    model_name = str(conf.WHISPER_MODEL or "base").strip()
    if whisper_state["model"] is None or whisper_state["loaded_name"] != model_name:
        try:
            import whisper
        except Exception as exc:
            raise RuntimeError(f"未安装 openai-whisper: {exc}") from exc
        whisper_state["model"] = whisper.load_model(model_name, device=_resolve_whisper_device())
        whisper_state["loaded_name"] = model_name
    return whisper_state["model"]


@app.post("/v1/upload_audio")
async def upload_audio(file: UploadFile = File(...)):
    try:
        # 直接读取音频数据到内存
        audio_bytes = await file.read()

        if _current_asr_mode() == "whisper":
            import tempfile
            from pathlib import Path

            model = _get_whisper_model()
            temp_path = None
            try:
                with tempfile.NamedTemporaryFile(suffix=".wav", delete=False) as tmp_file:
                    temp_path = Path(tmp_file.name)
                    tmp_file.write(audio_bytes)
                import faust_backend.config_loader as conf
                result = model.transcribe(
                    str(temp_path),
                    language=str(conf.WHISPER_LANGUAGE or "").strip() or None,
                    prompt=str(conf.WHISPER_PROMPT or "").strip() or None,
                    temperature=float(conf.WHISPER_TEMPERATURE or 0.0),
                    best_of=int(conf.WHISPER_BEST_OF or 5),
                    beam_size=int(conf.WHISPER_BEAM_SIZE or 5),
                    fp16=bool(conf.WHISPER_FP16) and torch.cuda.is_available(),
                )
                return {
                    "status": "success",
                    "filename": file.filename or "uploaded_audio",
                    "text": str((result or {}).get("text") or "").strip(),
                    "raw": result,
                    "mode": "whisper"
                }
            except Exception as exc:
                return {
                    "status": "error",
                    "filename": file.filename or "uploaded_audio",
                    "message": f"Whisper ASR 失败: {exc}"
                }
            finally:
                if temp_path is not None:
                    try:
                        temp_path.unlink(missing_ok=True)
                    except Exception:
                        pass

        # 使用 soundfile 或 librosa 直接从内存中解析音频
        import io
        try:
            import soundfile as sf
            # 直接从内存中读取音频数据
            audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
            log.info("音频数据形状: %s, 采样率: %s", audio_data.shape, sample_rate)
        except ImportError:
            log.info("soundfile 不可用，尝试使用 librosa")
            try:
                import librosa
                audio_data, sample_rate = librosa.load(io.BytesIO(audio_bytes), sr=16000)
                log.info(f"音频数据形状: {audio_data.shape}, 采样率: {sample_rate}")
            except ImportError:
                return {
                    "status": "error",
                    "message": "需要安装 soundfile 或 librosa 库来处理音频"
                }

        # 进行ASR处理 - 直接传入音频数组
        with torch.no_grad():
            # 确保为单通道 float32 numpy 数组（模型期望 float32）
            try:
                audio_data = np.asarray(audio_data)
                if audio_data.ndim > 1:
                    # 转为单通道（平均各声道）
                    audio_data = np.mean(audio_data, axis=1)
                audio_data = audio_data.astype('float32')
            except Exception as e:
                log.warning(f"处理音频数组时出错（类型/维度转换）：{e}")

            # 语音识别 - 传入 numpy 数组而不是文件路径
            asr_result = model_state["asr_model"].generate(
                input=audio_data,
                dtype="float32"
            )

            # 添加标点符号（标点模型通常期望文本输入；不要给它错误的 dtype）
            if asr_result and len(asr_result) > 0:
                text_input = asr_result[0]["text"]
                final_result = None
                try:
                    # 不传 dtype，这里传入文本让标点模型自行处理tokenization
                    final_result = model_state["punc_model"].generate(
                        input=text_input
                    )
                except Exception as e:
                    # 如果标点模型失败，记录并回退到未标点的文本
                    log.warning(f"标点模型处理失败，回退到原始文本: {e}")

                return {
                    "status": "success",
                    "filename": file.filename or "uploaded_audio",
                    "text": (final_result[0]["text"] if final_result else text_input)
                }
            else:
                return {
                    "status": "error",
                    "filename": file.filename or "uploaded_audio",
                    "message": "语音识别失败"
                }

    except Exception as e:
        log.error(f"处理音频时出错: {str(e)}")
        return {
            "status": "error",
            "message": str(e)
        }
if __name__ == '__main__':
    import uvicorn

    uvicorn.run(app, host="127.0.0.1", port=1000)