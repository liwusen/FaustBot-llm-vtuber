from fastapi import APIRouter, HTTPException, Query
import faust_backend.plugin_market as plugin_market
from faust_backend.runtime import state
from faust_backend.runtime.lifecycle import rebuild_runtime, _sync_plugin_trigger_filters

router = APIRouter(tags=["admin-plugins"])
router.description = "Plugin 管理：列出/重载/启用/禁用/配置/安装（市场/ZIP）/打包/删除插件"



@router.get("/faust/admin/plugins/assets")
async def admin_plugins_assets():
    """返回所有插件注册的前端资源清单（JS/CSS）。"""
    pm = state.plugin_manager
    if not pm:
        return {"status": "ok", "assets": []}
    try:
        assets = pm.collect_frontend_assets()
        return {"status": "ok", "assets": assets}
    except Exception as e:
        return {"status": "ok", "assets": [], "error": str(e)}

@router.get("/faust/admin/plugins")
async def admin_list_plugins():
    pm = state.plugin_manager
    return {"status": "ok", "items": pm.list_plugins() if pm else [], "manual_reload_only": True}


@router.post("/faust/admin/plugins/reload")
async def admin_reload_plugins(payload: dict | None = None):
    pm = state.plugin_manager
    if pm:
        summary = pm.reload()
        _sync_plugin_trigger_filters()
    else:
        summary = {"error": "plugin_manager not initialized"}
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    reset_dialog = bool((payload or {}).get("reset_dialog", True))
    no_initial_chat = bool((payload or {}).get("no_initial_chat", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {
        "status": "ok",
        "reload": summary,
        "runtime": runtime_info,
        "items": pm.list_plugins() if pm else [],
        "manual_reload_only": True,
    }


@router.get("/faust/admin/plugins/hot-reload")
async def admin_plugins_hot_reload_status():
    return {"status": "ok", "manual_reload_only": True, "enabled": False}


@router.post("/faust/admin/plugins/heartbeat")
async def admin_plugins_heartbeat_once():
    result = state.plugin_manager.heartbeat_tick() if state.plugin_manager else {}
    return {"status": "ok", "result": result}


@router.post("/faust/admin/plugins/hot-reload/start")
async def admin_plugins_hot_reload_start(payload: dict | None = None):
    return {"status": "ok", "manual_reload_only": True,
            "detail": "已禁用自动轮询热重载，请使用手动重载接口 /faust/admin/plugins/reload"}


@router.post("/faust/admin/plugins/hot-reload/stop")
async def admin_plugins_hot_reload_stop():
    return {"status": "ok", "manual_reload_only": True, "detail": "当前仅支持手动重载"}


@router.post("/faust/admin/plugins/{plugin_id}/enable")
async def admin_enable_plugin(plugin_id: str, payload: dict | None = None):
    if state.plugin_manager:
        state.plugin_manager.set_plugin_enabled(plugin_id, True)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    reset_dialog = bool((payload or {}).get("reset_dialog", True))
    no_initial_chat = bool((payload or {}).get("no_initial_chat", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": True, "runtime": runtime_info}


@router.post("/faust/admin/plugins/{plugin_id}/disable")
async def admin_disable_plugin(plugin_id: str, payload: dict | None = None):
    if state.plugin_manager:
        state.plugin_manager.set_plugin_enabled(plugin_id, False)
    apply_runtime = bool((payload or {}).get("apply_runtime", True))
    reset_dialog = bool((payload or {}).get("reset_dialog", True))
    no_initial_chat = bool((payload or {}).get("no_initial_chat", True))
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {"status": "ok", "plugin_id": plugin_id, "enabled": False, "runtime": runtime_info}


@router.get("/faust/admin/plugins/{plugin_id}/config")
async def admin_get_plugin_config(plugin_id: str):
    config = state.plugin_manager.get_plugin_config_snapshot(plugin_id) if state.plugin_manager else {}
    return {"status": "ok", "plugin_id": plugin_id, "config": config}


@router.post("/faust/admin/plugins/{plugin_id}/config")
async def admin_set_plugin_config(plugin_id: str, payload: dict | None = None):
    body = payload or {}
    values = body.get("values") or {}
    apply_runtime = bool(body.get("apply_runtime", True))
    reset_dialog = bool(body.get("reset_dialog", False))
    no_initial_chat = bool(body.get("no_initial_chat", True))
    config_snapshot = state.plugin_manager.set_plugin_config_values(plugin_id, values) if state.plugin_manager else {}
    if state.plugin_manager:
        reload_summary = state.plugin_manager.reload()
        _sync_plugin_trigger_filters()
    else:
        reload_summary = {}
    runtime_info = None
    if apply_runtime:
        runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
    return {
        "status": "ok",
        "plugin_id": plugin_id,
        "config": config_snapshot,
        "reload": reload_summary,
        "runtime": runtime_info,
    }


@router.get("/faust/admin/plugin-market/catalog")
async def admin_plugin_market_catalog(index_url: str | None = Query(default=None)):
    try:
        data = plugin_market.fetch_catalog(index_url=index_url)
        return {"status": "ok", **data}
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件市场读取失败: {e}")


@router.post("/faust/admin/plugin-market/install")
async def admin_plugin_market_install(payload: dict | None = None):
    body = payload or {}
    plugin_id = str(body.get("plugin_id") or body.get("id") or "").strip()
    index_url = body.get("index_url") or body.get("market_url")
    overwrite = bool(body.get("overwrite", False))
    apply_runtime = bool(body.get("apply_runtime", True))
    reset_dialog = bool(body.get("reset_dialog", False))
    no_initial_chat = bool(body.get("no_initial_chat", True))
    if not plugin_id:
        raise HTTPException(status_code=400, detail="缺少 plugin_id")
    try:
        plugins_dir = state.plugin_manager.plugins_dir if state.plugin_manager else None
        install_info = plugin_market.install_plugin_from_catalog(
            plugin_id=plugin_id, plugins_dir=plugins_dir,
            index_url=index_url, overwrite=overwrite)
        if state.plugin_manager:
            reload_summary = state.plugin_manager.reload()
            _sync_plugin_trigger_filters()
        else:
            reload_summary = {}
        runtime_info = None
        if apply_runtime:
            runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
        return {
            "status": "ok", "install": install_info, "reload": reload_summary,
            "runtime": runtime_info, "items": state.plugin_manager.list_plugins() if state.plugin_manager else [],
        }
    except plugin_market.PluginAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件安装失败: {e}")


@router.post("/faust/admin/plugins/install-zip")
async def admin_plugins_install_zip(payload: dict | None = None):
    body = payload or {}
    zip_path = str(body.get("zip_path") or "").strip()
    expected_plugin_id = str(body.get("plugin_id") or "").strip() or None
    overwrite = bool(body.get("overwrite", False))
    apply_runtime = bool(body.get("apply_runtime", True))
    reset_dialog = bool(body.get("reset_dialog", False))
    no_initial_chat = bool(body.get("no_initial_chat", True))
    if not zip_path:
        raise HTTPException(status_code=400, detail="缺少 zip_path")
    try:
        plugins_dir = state.plugin_manager.plugins_dir if state.plugin_manager else None
        install_info = plugin_market.install_plugin_from_zip(
            zip_path=zip_path, plugins_dir=plugins_dir,
            overwrite=overwrite, expected_plugin_id=expected_plugin_id)
        if state.plugin_manager:
            reload_summary = state.plugin_manager.reload()
            _sync_plugin_trigger_filters()
        else:
            reload_summary = {}
        runtime_info = None
        if apply_runtime:
            runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
        return {
            "status": "ok", "install": install_info, "reload": reload_summary,
            "runtime": runtime_info, "items": state.plugin_manager.list_plugins() if state.plugin_manager else [],
        }
    except plugin_market.PluginAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"ZIP 插件安装失败: {e}")


@router.post("/faust/admin/plugins/package-zip")
async def admin_plugins_package_zip(payload: dict | None = None):
    body = payload or {}
    plugin_id = str(body.get("plugin_id") or body.get("id") or "").strip()
    output_dir = body.get("output_dir")
    zip_name = body.get("zip_name")
    if not plugin_id:
        raise HTTPException(status_code=400, detail="缺少 plugin_id")
    try:
        plugins_dir = state.plugin_manager.plugins_dir if state.plugin_manager else None
        package_info = plugin_market.package_plugin_to_zip(
            plugin_id=plugin_id, plugins_dir=plugins_dir,
            output_dir=output_dir, zip_name=zip_name)
        return {"status": "ok", "package": package_info}
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件打包失败: {e}")


@router.delete("/faust/admin/plugins/{plugin_id}")
async def admin_delete_plugin(plugin_id: str, apply_runtime: bool = True, reset_dialog: bool = False, no_initial_chat: bool = True):
    try:
        plugins_dir = state.plugin_manager.plugins_dir if state.plugin_manager else None
        state_file = state.plugin_manager.state_file if state.plugin_manager else None
        delete_info = plugin_market.delete_installed_plugin(
            plugin_id=plugin_id, plugins_dir=plugins_dir, state_file=state_file)
        if state.plugin_manager:
            reload_summary = state.plugin_manager.reload()
            _sync_plugin_trigger_filters()
        else:
            reload_summary = {}
        runtime_info = None
        if apply_runtime:
            runtime_info = await rebuild_runtime(reset_dialog=reset_dialog, no_initial_chat=no_initial_chat)
        return {
            "status": "ok", "deleted": delete_info, "reload": reload_summary,
            "runtime": runtime_info, "items": state.plugin_manager.list_plugins() if state.plugin_manager else [],
        }
    except plugin_market.PluginMarketError as e:
        raise HTTPException(status_code=400, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=500, detail=f"插件删除失败: {e}")
