from __future__ import annotations

from fastapi import HTTPException

from faust_backend.araya_runtime import get_araya_runtime

from faust_backend.logger import get_logger

log = get_logger("faust.araya_api")
def register_araya_routes(app):
    @app.get("/faust/araya/status")
    async def araya_status():
        log.debug("Received request for Araya status")
        return {"status": "ok", "araya": get_araya_runtime(refresh=True).get_status()}

    @app.post("/faust/araya/trigger")
    async def araya_trigger(payload: dict | None = None):
        log.debug("Received request to trigger Araya with payload: %s", payload)
        runtime = get_araya_runtime(refresh=True)
        reason = str((payload or {}).get("reason") or "manual").strip() or "manual"
        try:
            result = await runtime.trigger_run(reason=reason)
            return {"status": "ok", "result": result}
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc))

    @app.post("/faust/araya/settings")
    async def araya_settings(payload: dict | None = None):
        log.debug("Received request to update Araya settings with payload: %s", payload)
        body = payload or {}
        runtime = get_araya_runtime(refresh=True)
        status = runtime.update_settings(
            enabled=body.get("enabled"),
            idle_minutes=body.get("idle_minutes"),
        )
        return {"status": "ok", "araya": status}