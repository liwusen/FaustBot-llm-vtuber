import json
import asyncio
import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import Response
import faust_backend.config_loader as conf
import faust_backend.speech_runtime as speech_runtime
import faust_backend.vad_runtime as vad_runtime
from faust_backend.runtime import state
from faust_backend.logger import get_logger

log = get_logger("faust.audio")

router = APIRouter(tags=["audio"])
router.description = "Audio / 语音：语音配置、VAD 状态 & WebSocket 推理、TTS 合成、ASR 转录"


@router.get("/faust/audio/config")
async def speech_config_get():
    conf.reload_configs()
    return {"status": "ok", "config": speech_runtime.frontend_speech_config()}


@router.get("/faust/audio/vad/status")
async def speech_vad_status_get():
    return await vad_runtime.vad_runtime.status()


@router.websocket("/faust/audio/ws/vad")
async def speech_vad_ws(websocket: WebSocket):
    await websocket.accept()
    await vad_runtime.vad_runtime.connection_opened()
    try:
        while True:
            data = await websocket.receive_bytes()
            audio = np.frombuffer(data, dtype=np.float32).copy()
            if len(audio) != vad_runtime.WINDOW_SIZE:
                continue
            result = await vad_runtime.vad_runtime.infer_frame(audio)
            await websocket.send_text(json.dumps(result, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("VAD WebSocket 错误: %s", e)
    finally:
        await vad_runtime.vad_runtime.connection_closed()
        try:
            await websocket.close()
        except Exception:
            pass


@router.post("/faust/audio/tts")
async def speech_tts_post(payload: dict):
    text = ""
    lang = None
    if isinstance(payload, dict):
        text = str(payload.get("text") or "").strip()
        lang = payload.get("lang") or payload.get("text_language")
    if not text:
        raise HTTPException(status_code=400, detail="缺少 TTS 文本")
    conf.reload_configs()
    try:
        audio_bytes, content_type = await speech_runtime.synthesize_tts(text, lang)
        return Response(content=audio_bytes, media_type=content_type)
    except speech_runtime.SpeechRuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"TTS 代理失败: {e}")


@router.post("/faust/audio/asr")
async def speech_asr_post(file: UploadFile = File(...)):
    conf.reload_configs()
    try:
        audio_bytes = await file.read()
        result = await asyncio.to_thread(
            speech_runtime.transcribe_audio,
            file.filename or "audio.wav",
            audio_bytes,
            file.content_type or "audio/wav",
        )
        return result
    except speech_runtime.SpeechRuntimeError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ASR 代理失败: {e}")
