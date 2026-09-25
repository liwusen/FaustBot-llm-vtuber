import json
import asyncio
import numpy as np
from fastapi import APIRouter, HTTPException, WebSocket, WebSocketDisconnect, UploadFile, File
from fastapi.responses import Response
import faust_backend.config_loader as conf
import faust_backend.speech_runtime as speech_runtime
from faust_backend.processors import get_processor_manager
from faust_backend.processors.builtin.vad import SAMPLE_RATE, VAD_THRESHOLD, WINDOW_SIZE
from faust_backend.processors.errors import ProcessorError
from faust_backend.runtime import state
from faust_backend.logger import get_logger

log = get_logger("faust.audio")

router = APIRouter(tags=["audio"])

VAD_DEGRADED_BACKOFF_SECONDS = 30.0



@router.get("/faust/audio/config")
async def speech_config_get():
    conf.reload_configs()
    return {"status": "ok", "config": speech_runtime.frontend_speech_config()}


@router.get("/faust/audio/vad/status")
async def speech_vad_status_get():
    # 旧字段（is_loaded/is_running/active_connections/unavailable_reason）保持不变，
    # 追加 Processor 视角的 state/last_error/pid/refcount
    snapshot = get_processor_manager().handle("VAD").status()
    return {
        "is_loaded": snapshot["state"] == "ACTIVE",
        "is_running": snapshot["refcount"] > 0,
        "active_connections": snapshot["refcount"],
        "sample_rate": SAMPLE_RATE,
        "window_size": WINDOW_SIZE,
        "threshold": VAD_THRESHOLD,
        "unavailable_reason": snapshot["last_error"],
        "state": snapshot["state"],
        "last_error": snapshot["last_error"],
        "pid": snapshot["pid"],
        "refcount": snapshot["refcount"],
    }


@router.websocket("/faust/audio/ws/vad")
async def speech_vad_ws(websocket: WebSocket):
    await websocket.accept()
    manager = get_processor_manager()
    requirer = f"vad_ws:{id(websocket)}"
    loop = asyncio.get_running_loop()
    lease = None
    needs_ready = True
    retry_after = 0.0
    last_error_text = ""
    try:
        try:
            lease = await manager.startRequire("VAD", requirer=requirer)   # 连接即持有引用
        except ProcessorError as e:
            # 依赖缺失（如未装 torch）等：连接保留，按降级帧回复，退避后重试
            last_error_text = str(e)
            retry_after = loop.time() + VAD_DEGRADED_BACKOFF_SECONDS
            log.error("VAD 不可用: %s", e)

        while True:
            data = await websocket.receive_bytes()
            audio = np.frombuffer(data, dtype=np.float32).copy()
            if len(audio) != WINDOW_SIZE:
                continue

            if lease is None:
                now = loop.time()
                if now < retry_after:
                    await websocket.send_text(
                        json.dumps(
                            {"is_speech": False, "probability": 0.0, "error": last_error_text or "VAD 正在恢复，请稍候"},
                            ensure_ascii=False,
                        )
                    )
                    continue
                try:
                    lease = await manager.startRequire("VAD", requirer=requirer)
                    needs_ready = True
                except ProcessorError as e:
                    last_error_text = str(e)
                    retry_after = now + VAD_DEGRADED_BACKOFF_SECONDS
                    log.error("VAD 不可用: %s", e)
                    await websocket.send_text(
                        json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                    )
                    continue

            if needs_ready:
                try:
                    await lease.wait_until_ready()
                    needs_ready = False
                except ProcessorError as e:
                    last_error_text = str(e)
                    retry_after = loop.time() + VAD_DEGRADED_BACKOFF_SECONDS
                    log.error("VAD 不可用: %s", e)
                    lease.release()
                    lease = None
                    await websocket.send_text(
                        json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                    )
                    continue
            elif lease.handle.state != "ACTIVE":
                # worker 崩溃或已被 prune 回收：换一条新 lease（自愈），失败则退避
                lease.release()
                lease = None
                try:
                    lease = await manager.startRequire("VAD", requirer=requirer)
                    await lease.wait_until_ready()
                    needs_ready = False
                    log.warning("VAD worker 已重启")
                except ProcessorError as e:
                    last_error_text = str(e)
                    retry_after = loop.time() + VAD_DEGRADED_BACKOFF_SECONDS
                    log.error("VAD 不可用: %s", e)
                    lease = None
                    await websocket.send_text(
                        json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                    )
                    continue

            try:
                result = await lease.invoke({"audio": audio})
            except ProcessorError as e:
                last_error_text = str(e)
                log.error("VAD invoke 失败: %s", e)
                lease.release()
                lease = None
                needs_ready = True
                await websocket.send_text(
                    json.dumps({"is_speech": False, "probability": 0.0, "error": last_error_text}, ensure_ascii=False)
                )
                continue
            await websocket.send_text(json.dumps(result, ensure_ascii=False))
    except WebSocketDisconnect:
        pass
    except Exception as e:
        log.error("VAD WebSocket 错误: %s", e)
    finally:
        if lease is not None:
            lease.release()
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
