import logging

log = logging.getLogger("faust.live_mode")

_live_mode_active = False

LIVE_MODE_EXCLUDED_TOOL_NAMES: set[str] = {
    "pythonExecTool",
    "sysExecTool",
    "triggerAddTool",
    "triggerRemoveTool",
    "triggerListTool",
    "installOpenClawSkillTool",
    "kbWriteTool",
    "writeTextFileTool",
}

_danmaku_blacklist: list[str] = []
_tts_blacklist: list[str] = []


def is_live_mode() -> bool:
    return _live_mode_active


def set_live_mode(active: bool) -> None:
    global _live_mode_active
    _live_mode_active = bool(active)
    log.info("直播模式: %s", "开启" if _live_mode_active else "关闭")


def get_excluded_tool_names() -> set[str]:
    return LIVE_MODE_EXCLUDED_TOOL_NAMES


def get_danmaku_blacklist() -> list[str]:
    return list(_danmaku_blacklist)


def set_danmaku_blacklist(words: list[str]) -> None:
    global _danmaku_blacklist
    _danmaku_blacklist = [str(w).strip() for w in words if str(w).strip()]
    log.info("弹幕黑名单已更新: %s", _danmaku_blacklist)


def get_tts_blacklist() -> list[str]:
    return list(_tts_blacklist)


def set_tts_blacklist(words: list[str]) -> None:
    global _tts_blacklist
    _tts_blacklist = [str(w).strip() for w in words if str(w).strip()]
    log.info("TTS 黑名单已更新: %s", _tts_blacklist)


def is_danmaku_blacklisted(text: str) -> bool:
    for word in _danmaku_blacklist:
        if word in text:
            return True
    return False


def is_tts_blacklisted(text: str) -> bool:
    for word in _tts_blacklist:
        if word in text:
            return True
    return False
