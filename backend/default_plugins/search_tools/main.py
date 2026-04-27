from __future__ import annotations

from typing import Any

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from langchain_community.utilities import WikipediaAPIWrapper

import faust_backend.config_loader as conf
from faust_backend.plugin_system import PluginContext, PluginManifest, ToolSpec
from faust_backend.searchapi_patched import SearchApiAPIWrapper


class Plugin:
    manifest = PluginManifest(
        plugin_id="search_tools",
        name="Search Tools Plugin",
        version="1.0.0",
        description="Provide SearchAPI and Wikipedia search tools",
        author="faust",
        homepage="",
        enabled=True,
        permissions=["tool:web-search", "tool:wiki-search"],
        priority=200,
    )

    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config(
            """
            SEARCH_ENGINE:str:SearchAPI引擎=google
            SEARCHAPI_API_KEY:str:SearchAPI Key(使用__MAIN_CONFIG__表示主配置)=__MAIN_CONFIG__
            ENABLE_SEARCHAPI:bool:启用SearchAPI搜索=true
            ENABLE_WIKIPEDIA:bool:启用Wikipedia搜索=true
            """
        )

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def register_tools(self, ctx: PluginContext):
        @tool
        def webSearchTool(query: str) -> str:
            """
            Description:
                使用SearchApi进行网络搜索，并返回搜索结果的摘要。
            Args:
                query (str): 需要搜索的查询字符串。
            Returns:
                str: 搜索结果的摘要。
            """
            if not bool(ctx.get_config("ENABLE_SEARCHAPI", True)):
                return "SearchAPI 搜索已在插件配置中禁用。"
            engine = str(ctx.get_config("SEARCH_ENGINE", "google") or "google")
            api_key_cfg = str(ctx.get_config("SEARCHAPI_API_KEY", "__MAIN_CONFIG__") or "__MAIN_CONFIG__").strip()
            api_key = conf.SEARCH_API_KEY if api_key_cfg == "__MAIN_CONFIG__" else api_key_cfg
            try:
                # 明确使用 patched 版本，规避原 SearchAPI 包装器已知问题
                wrapper = SearchApiAPIWrapper(engine=engine, searchapi_api_key=api_key)
                print("[plugin.search_tools.webSearchTool] Searching web for query:", query)
                return wrapper.run(query=query)
            except Exception as e:
                return f"SearchAPI 搜索失败: {str(e)}"

        @tool
        def wikiSearchTool(query: str) -> str:
            """
            Description:
                使用Wikipedia进行搜索，并返回搜索结果的摘要。
            Args:
                query (str): 需要搜索的查询字符串。
            Returns:
                str: 搜索结果的摘要。
            """
            if not bool(ctx.get_config("ENABLE_WIKIPEDIA", True)):
                return "Wikipedia 搜索已在插件配置中禁用。"
            try:
                wrapper = WikipediaAPIWrapper()
                print("[plugin.search_tools.wikiSearchTool] Searching Wikipedia for query:", query)
                return wrapper.run(query=query)
            except Exception as e:
                return f"Wikipedia 搜索失败: {str(e)}"

        return [
            ToolSpec(
                name="webSearchTool",
                tool=webSearchTool,
                enabled_by_default=True,
                description=webSearchTool.__doc__,
            ),
            ToolSpec(
                name="wikiSearchTool",
                tool=wikiSearchTool,
                enabled_by_default=True,
                description=wikiSearchTool.__doc__,
            ),
        ]

    def register_middlewares(self, ctx: PluginContext):
        return []

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "search_tools"}

    def Heartbeat(self, ctx: PluginContext) -> None:
        pass


def get_plugin() -> Plugin:
    return Plugin()
