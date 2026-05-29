print("Booting...")
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

@asynccontextmanager
async def lifespan(app: FastAPI):
    log.info("正在加载 ASR 模型...")
    global model
    if torch.cuda.is_available():
        log.info("检测到 CUDA 可用，使用 GPU 加速")
        model = AutoModel(
            model="FunAudioLLM/Fun-ASR-Nano-2512",
            trust_remote_code=True,
            remote_code="./model.py",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device="cuda:0",
            disable_update=True
        )
    else:
        log.info("CUDA 不可用，使用 CPU 进行推理（性能可能较差）")
        model = AutoModel(
            model="FunAudioLLM/Fun-ASR-Nano-2512",
            trust_remote_code=True,
            remote_code="./model.py",
            vad_model="fsmn-vad",
            vad_kwargs={"max_single_segment_time": 30000},
            device="cpu",
            disable_update=True
        )
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
@app.post("/v1/upload_audio")
async def upload_audio(file: UploadFile = File(...)):
    try:
        audio_bytes = await file.read()

        import io
        import soundfile as sf
        # 直接从内存中读取音频数据
        audio_data, sample_rate = sf.read(io.BytesIO(audio_bytes))
        log.info("音频数据形状: %s, 采样率: %s", audio_data.shape, sample_rate)

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
            asr_result = model.generate(
                input=audio_data,
                dtype="float32"
            )
            return {
                "status": "success",
                "filename": file.filename or "uploaded_audio",
                "text": asr_result[0]["text"],
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