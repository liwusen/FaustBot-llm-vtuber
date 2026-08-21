import json
import asyncio
import uuid

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.tools.hil import HILRequest
from faust_backend.tools._patch_utils import install_skill_from_slug
import faust_backend.config_loader as conf

import faust_backend.skill_manager as skill_manager
from faust_backend.runtime import state


@register
@tool
async def listSkills(show_detail: bool = False) -> str:
    """列出当前 Agent 已安装的所有可用技能（Skill）。
    返回技能的 slug 和描述，帮助了解 Agent 具备哪些领域能力。
    如需某个技能的详细说明，用 read("skill://<slug>/SKILL.md") 读取。

    Args:
        show_detail (bool): 是否显示每个技能的详细说明和 SKILL.md 路径。
    Returns:
        str: 已安装技能列表。
    """
    try:
        skills = skill_manager.list_skills(agent_name=state.AGENT_NAME)
        if not skills:
            return "当前没有已安装的技能。"

        lines = [f"已安装 {len(skills)} 个技能："]
        for s in skills:
            slug = str(s.get("slug") or "?")
            name = str(s.get("name") or slug)
            version = str(s.get("version") or "0.0.0")
            enabled = s.get("enabled", True)
            status = "✅ 启用" if enabled else "⛔ 禁用"
            desc = str(s.get("description") or "")
            if show_detail and desc:
                lines.append(f"  - {slug} ({name} v{version}) {status}\n    描述：{desc}")
            else:
                summary = f" - {desc[:80]}" if desc else ""
                lines.append(f"  - {slug} {status}{summary}")
        if not show_detail:
            lines.append("\n提示：使用 read(\"skill://<slug>/SKILL.md\") 查看技能完整说明。")
        return "\n".join(lines)
    except Exception as e:
        return f"技能列表读取失败: {e}"
