import json
from fastapi import APIRouter, WebSocket, WebSocketDisconnect
from faust_backend.logger import subscribe_ws, unsubscribe_ws, get_logger

log = get_logger("faust.logger_ws")

router = APIRouter(tags=["logger"])



@router.websocket("/faust/logger/ws")
async def logger_websocket(websocket: WebSocket):
    await websocket.accept()
    log.info("日志 WebSocket 客户端已连接")
    q = await subscribe_ws()
    try:
        while True:
            payload = await q.get()
            try:
                await websocket.send_text(json.dumps(payload, ensure_ascii=False))
            except WebSocketDisconnect:
                break
            except Exception:
                break
    except WebSocketDisconnect:
        pass
    finally:
        await unsubscribe_ws(q)
        log.info("日志 WebSocket 客户端已断开")
