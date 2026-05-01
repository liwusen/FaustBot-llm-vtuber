import json
import asyncio

from langchain.tools import tool

from faust_backend.tools._registry import register
import faust_backend.minecraft_client as minecraft_client


@register
@tool
def minecraftCommandTool(command_json: str) -> str:
    """
    Description:
        向 Minecraft 操作系统发送一条 JSON 命令，并返回执行结果。
        这是 FaustBot 操作 Minecraft 的主入口工具。
    Args:
        command_json (str): JSON 格式命令，例如 {"name":"get-mobs-around","args":{"radius":5}}
    Returns:
        str(json): 执行结果 JSON。
    """
    try:
        payload = json.loads(command_json)
        name = payload.get("name")
        args = payload.get("args") or {}
        if not name:
            return json.dumps({"ok": False, "error": "missing command name"}, ensure_ascii=False)
        result = asyncio.run(minecraft_client.send_command(name, args))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@register
@tool
def minecraftConnectTool(host: str, port: int, username: str, version: str = "") -> str:
    """
    Description:
        连接到 Minecraft 服务器。Agent 应自行决定何时加入服务器。
    Args:
        host (str): 服务器地址。
        port (int): 服务器端口。
        username (str): Bot 用户名。
        version (str): 可选协议版本。
    Returns:
        str(json): 连接结果。
    """
    try:
        result = asyncio.run(minecraft_client.connect_server(host, port, username, version or None))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@register
@tool
def minecraftStatusTool() -> str:
    """
    Description:
        获取当前 Minecraft Bot 状态，包括连接、坐标、血量、饱食度和附近实体等。
    Args:
        None
    Returns:
        str(json): Bot 状态 JSON。
    """
    try:
        result = asyncio.run(minecraft_client.get_status())
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)


@register
@tool
def minecraftDisconnectTool(reason: str = "disconnect requested") -> str:
    """
    Description:
        断开当前 Minecraft 服务器连接。
    Args:
        reason (str): 断开原因。
    Returns:
        str(json): 断开结果 JSON。
    """
    try:
        result = asyncio.run(minecraft_client.disconnect_server(reason))
        return json.dumps(result, ensure_ascii=False)
    except Exception as e:
        return json.dumps({"ok": False, "error": str(e)}, ensure_ascii=False)
