"""运行时重建（含 reset_dialog 与引导对话）与 models.dev 目录刷新。

自 ``runtime/lifecycle.py`` 整块挖出（P4 布局拆分）。``_rebuild_runtime_locked``
按「保守拆分」原则原样搬运：208 行的巨函数一行没动，连 TODO 一并搬走。
"""

from __future__ import annotations

import asyncio
import os

import aiosqlite
from langgraph.checkpoint.memory import InMemorySaver
from langgraph.checkpoint.sqlite.aio import AsyncSqliteSaver

import faust_backend.agent.araya.runtime as araya_runtime
import faust_backend.agent.runtime.state as state
import faust_backend.core.config as conf
import faust_backend.core.frontend.api as backend2frontend
import faust_backend.llm_tools as llm_tools
from faust_backend.agent.prompt.seed import _checkpoint_messages, _seed_system_prompt
from faust_backend.agent.runtime.build import (
    _create_agent_with_extensions,
    _rebuild_subagent_manager,
)
from faust_backend.agent.runtime.invoke import _invoke_agent_unlocked
from faust_backend.agent.runtime.lock import AgentLockBusy, TimedAgentLock
from faust_backend.core.config import args
from faust_backend.core.log import get_logger
from faust_backend.tools.vfs import get_faustbot_vfs, refresh_runtime_nodes

log = get_logger("faust.lifecycle")


async def rebuild_runtime(*, reset_dialog: bool = False, no_initial_chat: bool = False):
    """重建运行时；拿不到主 Agent 锁时返回 busy 结果，绝不无限期等待。

    修复前这里是裸 `async with state.agent_lock`（全工程唯一一处无超时获取）：
    斜杠命令 /clear /thinking /effort 在 WS 接收循环里 await 它，Agent 一忙就把
    整个输入循环（连同 interrupt 控制消息）一起冻住，管理接口同样挂起且无诊断。
    锁忙不等于运行时损坏：这里只报告状态，不清空 state.agent。

    调用位置决定锁的归属：斜杠命令/管理路由把它放在调度器的 exclusive 回合里
    （锁已由 worker 持有，取锁是 no-op），启动期与独立重建则在回合外自己取锁。
    """
    try:
        return await _rebuild_runtime_locked(
            reset_dialog=reset_dialog,
            no_initial_chat=no_initial_chat,
        )
    except AgentLockBusy as e:
        log.error("重建运行时未取得主 Agent 锁: %s", e)
        return {
            "agent_name": state.AGENT_NAME,
            "agent_root": state.AGENT_ROOT,
            "initial_chat_skipped": True,
            "ready": False,
            "status": "busy",
            "error": str(e),
        }


async def _rebuild_runtime_locked(
    *, reset_dialog: bool = False, no_initial_chat: bool = False
):  # TODO: 让逻辑更加清晰
    log.info(
        "正在重建运行时，reset_dialog=%s, no_initial_chat=%s",
        reset_dialog,
        no_initial_chat,
    )
    _needs_initial_chat = False
    async with TimedAgentLock("rebuild_runtime"):
        try:
            conf.reload_configs()
            from faust_backend.agent.runtime import state as runtime_state
            from faust_backend.llm.provider import (
                get_main_credentials,
                try_main_openai_credentials,
            )

            # models.dev 目录：后台刷新 + 重新路由，不阻塞启动。
            # 失败只记日志（继续用陈旧缓存），绝不阻断 Agent 启动。
            asyncio.create_task(
                refresh_models_dev_catalog(), name="models_dev_refresh"
            )

            _model_name, _api_key, _api_base = get_main_credentials(
                runtime_state.get_model_providers()
            )
            os.environ["DEEPSEEK_API_KEY"] = _api_key
            os.environ["SEARCHAPI_API_KEY"] = conf.SEARCH_API_KEY
            # OPENAI_* 环境变量只服务于「仍假定 OpenAI 协议」的第三方库。
            # 主模型是 Anthropic/Gemini 格式时不能把它的 base_url 冒充成 OpenAI
            # 端点（会让这些库打到必然 404 的路径），此时宁可不设置并要求调用方
            # 显式使用 get_main_openai_credentials()。
            _openai_creds = try_main_openai_credentials(
                runtime_state.get_model_providers()
            )
            if _openai_creds is not None:
                _oa_model, _oa_key, _oa_base = _openai_creds
                os.environ["OPENAI_API_KEY"] = _oa_key
                os.environ["OPENAI_BASE_URL"] = _oa_base
            else:
                log.info(
                    "主模型不是 OpenAI Chat 兼容格式，未设置 OPENAI_BASE_URL"
                    "（依赖 OpenAI 协议的内部功能会明确报错）"
                )
            state.AGENT_NAME = conf.AGENT_NAME
            state.AGENT_ROOT = os.path.join(
                conf.CONFIG_ROOT, "agents", f"{state.AGENT_NAME}"
            )
            log.info("重建目标 Agent: %s", state.AGENT_NAME)
            # MD_BLOCK_ENABLED 在此判断并生效：后续 _create_agent_with_extensions
            # 会经 get_tools_for_agent 按该开关决定是否注册 RenderMarkdownBlock
            log.info(
                "Markdown 内容块工具 (RenderMarkdownBlock): %s",
                "启用" if conf.MD_BLOCK_ENABLED else "禁用",
            )

            # ---Templates Makeup---
            import faust_backend.admin_runtime as admin_runtime

            sync_result = admin_runtime.sync_template_files(state.AGENT_NAME)
            if any(sync_result.values()):
                updated = [k for k, v in sync_result.items() if v]
                log.info("模板文件已同步: %s", ", ".join(updated))
            state.makeup_init_prompt()
            llm_tools.refresh_runtime_paths()
            await refresh_runtime_nodes(await get_faustbot_vfs(refresh=True))

            # ---mcp---
            from faust_backend.mcp_manager import get_mcp_manager

            mcp_manager = get_mcp_manager()
            mcp_manager.load_config(getattr(conf, "MCP_SERVERS", {}) or {})
            await mcp_manager.sync_servers()

            # ---Araya Runtime---
            araya_runtime.get_araya_runtime(refresh=True).refresh_target_agent()

            # ---Plugins---
            pm = state.plugin_manager
            if (
                pm and pm.needs_reload()
            ):  # 不在每次重建时都重载插件，而是仅在插件配置或文件变更时才重载
                log.info("插件配置或文件变更，正在重载插件...")
                plugin_reload = await pm.reload(force=True)
                log.info("插件重载摘要: %s", plugin_reload)
                # 说明：_sync_plugin_trigger_filters 归 app/services.py（进程装配层），
                # 这里按需局部导入，避免 agent/ 在模块层反向依赖 app/。
                from faust_backend.app.services import _sync_plugin_trigger_filters

                _sync_plugin_trigger_filters()
                backend2frontend.FrontEndReloadPluginAssets()

            # ---langchain Agent---
            if not args.save_in_memory:
                try:
                    if state.conn is not None:
                        await state.conn.commit()
                        await state.conn.close()
                except Exception:
                    pass
                os.makedirs(state.AGENT_ROOT, exist_ok=True)
                state.conn = await aiosqlite.connect(
                    os.path.join(state.AGENT_ROOT, "faust_checkpoint.db")
                )
                state.checkpointer = AsyncSqliteSaver(conn=state.conn)
                log.info("已初始化 SQLite Checkpoint")
            else:
                state.checkpointer = InMemorySaver()
            log.info("Checkpoint 已为重建就绪")
            from faust_backend.agent.runtime import state as runtime_state
            from faust_backend.llm.provider import get_default_subagent_model
            _providers = runtime_state.get_model_providers()
            state.agent = await _create_agent_with_extensions(
                model_name=_providers.main_model,
                checkpointer=state.checkpointer,
            )
            try:
                _sub_model = get_default_subagent_model(_providers)
            except ValueError:
                _sub_model = _providers.main_model
            await _rebuild_subagent_manager(model_name=_sub_model)
            log.debug("Agent 已为重建重新创建")
            checkpoint_exists = (not args.save_in_memory) and state.has_checkpoint_db(
                state.AGENT_ROOT
            )
            if no_initial_chat and checkpoint_exists:
                log.info("运行时重建跳过初始对话")
                state.set_runtime_state(ready=True, status="ready")
                return {
                    "agent_name": state.AGENT_NAME,
                    "agent_root": state.AGENT_ROOT,
                    "initial_chat_skipped": True,
                    "ready": True,
                    "status": state.RUNTIME_STATUS,
                    "error": "",
                }
            _needs_initial_chat = True
        except Exception as e:
            state.agent = None
            state.set_runtime_state(
                ready=False, status="waiting_for_config", error=str(e)
            )
            log.warning("运行时重建降级: %s", e)
            return {
                "agent_name": state.AGENT_NAME,
                "agent_root": state.AGENT_ROOT,
                "initial_chat_skipped": True,
                "ready": False,
                "status": state.RUNTIME_STATUS,
                "error": str(e),
            }

    if _needs_initial_chat:
        try:
            if reset_dialog:
                # 只写状态、不调模型：system-only 的模型请求对 Gemini/Anthropic
                # 都是非法请求（contents are required / messages 不能为空）。
                await _seed_system_prompt(state.agent)
            else:
                # 全新 checkpoint（首次安装、换角色后的新库）里没有任何 System
                # 消息，角色 Prompt 就永远不会进上下文；先补齐再发 bootstrap。
                if not await _checkpoint_messages(state.agent):
                    await _seed_system_prompt(state.agent)
                await _invoke_agent_unlocked(
                    state.agent,
                    {
                        "messages": [
                            {
                                "role": "user",
                                "content": (
                                    f"请执行启动流程(Bootstrap 流程)[如有]"
                                    f"你可以读取agents/{state.AGENT_NAME}/AGENT.md、ROLE.md、COREMEMORY.md、TASK.md等文件"
                                    f"来获取最新的设定内容。\n 这一条对话无需写入日记"
                                ),
                            }
                        ]
                    },
                )
            log.info("运行时重建完成")
            state.set_runtime_state(ready=True, status="ready")
            return {
                "agent_name": state.AGENT_NAME,
                "agent_root": state.AGENT_ROOT,
                "initial_chat_skipped": False,
                "ready": True,
                "status": state.RUNTIME_STATUS,
                "error": "",
            }
        except Exception as e:
            state.agent = None
            state.set_runtime_state(
                ready=False, status="waiting_for_config", error=str(e)
            )
            log.warning("运行时重建降级: %s", e)
            return {
                "agent_name": state.AGENT_NAME,
                "agent_root": state.AGENT_ROOT,
                "initial_chat_skipped": True,
                "ready": False,
                "status": state.RUNTIME_STATUS,
                "error": str(e),
            }

    return {
        "agent_name": state.AGENT_NAME,
        "agent_root": state.AGENT_ROOT,
        "initial_chat_skipped": True,
        "ready": True,
        "status": state.RUNTIME_STATUS,
        "error": "",
    }


async def refresh_models_dev_catalog(*, force: bool = False) -> dict:
    """后台刷新 models.dev 目录并把结果应用到所有 Provider（不阻塞启动）。

    只有配置真的变化时才写回 `provider.private.json`，避免无谓的磁盘写入。
    任何异常都只记日志：目录缺失时格式会回退到通用启发式，Agent 照常工作。
    """
    import faust_backend.llm.models_dev as models_dev

    providers = state.get_model_providers()
    snapshot = None
    try:
        snapshot = providers.model_dump()
    except Exception:  # noqa: BLE001 - 测试替身/受限对象没有 model_dump
        snapshot = None
    try:
        catalog = (
            await models_dev.refresh_catalog()
            if force
            else await models_dev.refresh_if_stale()
        )
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把异常抛给事件循环
        log.warning("刷新 models.dev 目录失败（继续使用已有缓存）: %s", exc)
        return {"status": "error", "error": str(exc)}
    if not catalog:
        log.info("models.dev 目录不可用，API 格式将回退到通用启发式")
        return {"status": "empty", "providers": 0}
    if not getattr(providers, "providers", None):
        return {"status": "ok", "providers": 0, "matched": 0, "results": []}
    results = models_dev.enrich_all(providers, catalog)
    after = providers.model_dump()
    if after != snapshot:
        try:
            conf.save_model_providers()
        except Exception as exc:  # noqa: BLE001
            log.warning("保存 models.dev 路由结果失败: %s", exc)
    matched = [item for item in results if item.get("provider_id")]
    log.info(
        "models.dev 目录已应用: %d 个 Provider 完成匹配（共 %d 个），路由模型 %d 个",
        len(matched),
        len(results),
        sum(int(item.get("routed") or 0) for item in results),
    )
    return {
        "status": "ok",
        "providers": len(results),
        "matched": len(matched),
        "results": results,
    }


def schedule_memory_record_sync(user_text: str, assistant_text: str) -> None:
    if not conf.KB_ENABLED:
        return
    if not str(user_text).strip() or not str(assistant_text).strip():
        return
    log.debug("调度记忆记录同步")

    async def _job():
        try:
            llm_tools.refresh_runtime_paths()
            from faust_backend.memory import get_memory

            result = await get_memory().add_chat_record(user_text, assistant_text)
            log.info("聊天记录已同步到记忆库: %s", result.get("path"))
        except Exception as exc:
            log.error("聊天记录同步到记忆库失败: %s", exc)

    asyncio.create_task(_job())
