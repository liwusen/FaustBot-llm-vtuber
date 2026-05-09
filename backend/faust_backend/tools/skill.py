import json
import asyncio
import uuid

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.tools.hil import HILRequest
from faust_backend.tools._patch_utils import install_skill_from_slug
import faust_backend.config_loader as conf


@register
@tool
async def installOpenClawSkillTool(slug: str, overwrite: bool = False) -> str:
    """
    Description:
        安装一个 OpenClaw Skill 到当前 Agent 的独立目录 agents/<agent>/skill.d/<slug>。
        安装前会触发前端 HIL 确认框，用户批准后才会真正下载与安装。
        下载 API:
        https://wry-manatee-359.convex.site/api/v1/download?slug=<NAME>

        Skill ZIP 结构要求：
        - _meta.json
        - SKILL.md
        - 其他文件
    Args:
        slug (str): skill 名称（slug）。
        overwrite (bool): 若已存在是否覆盖安装。
    Returns:
        str: 安装结果说明。
    """

    slug = str(slug or "").strip()
    if not slug:
        return "安装失败：slug 不能为空。"

    approved, reason = await HILRequest(
        id=f"skill_install_{uuid.uuid4().hex}",
        title=f"允许安装 Skill: {slug} ?",
        summary=(
            f"Agent 请求安装 Skill：{slug}\n"
            f"目标目录: agents/{conf.AGENT_NAME}/skill.d/{slug}\n"
            f"来源: https://wry-manatee-359.convex.site/api/v1/download?slug={slug}\n"
            f"overwrite={bool(overwrite)}"
        ),
    )
    if not approved:
        return f"用户拒绝安装 Skill，slug={slug}，原因={reason}"

    try:
        result = await asyncio.to_thread(install_skill_from_slug, slug, overwrite)
        return f"Skill 安装成功: {json.dumps(result, ensure_ascii=False)}"
    except Exception as e:
        return f"Skill 安装失败: {str(e)}"
