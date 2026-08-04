import json
from pathlib import Path

PROJECT_ROOT = Path(__file__).resolve().parents[2]
SKILL_TEMPLATE = PROJECT_ROOT / "backend" / "skill_template"
AGENT_TEMPLATE = PROJECT_ROOT / "backend" / "agents_template" / "faust"


def test_minecraft_skill_exists_and_has_meta():
    skill_dir = SKILL_TEMPLATE / "minecraft"
    assert (skill_dir / "SKILL.md").exists()
    meta = json.loads((skill_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "minecraft"
    assert meta["builtin"] is True
    assert meta["description"].strip()
    assert meta["usage"].strip()


def test_minecraft_skill_contains_command_handbook():
    text = (SKILL_TEMPLATE / "minecraft" / "SKILL.md").read_text(encoding="utf-8")
    for cmd in ("connect-server", "mine-block", "craft-item", "open-chest", "use-bed"):
        assert f"`{cmd}`" in text
    assert "行为准则" in text
    assert "失败处理策略" in text
    assert "事件触发" in text


def test_agent_md_keeps_core_tools_and_points_to_skills():
    """核心工具详解保留在主 Prompt；TASK.md 指引指向内置 skill。"""
    text = (AGENT_TEMPLATE / "AGENT.md").read_text(encoding="utf-8")
    # 核心工具详解仍在 AGENT.md（不移入 skill）
    assert "### 1. read — 通用读取" in text
    assert "### 2. execute — 执行代码" in text
    # 行为核心规则保留
    for rule in ("不要在输出中使用 Markdown", "function call", "listAvailableMotionsTool",
                 "直播模式", "TASK.md", "skill.d"):
        assert rule in text
    # Minecraft/Nimble 指引指向内置 skill
    assert "skill://minecraft" in text
    assert "skill://nimble-window" in text


def test_task_md_slimmed():
    """TASK.md 移除 Minecraft/Nimble/重复速查，保留指引。"""
    text = (AGENT_TEMPLATE / "TASK.md").read_text(encoding="utf-8")
    # 详细段已移出
    assert "## Minecraft 操作系统说明" not in text
    assert "## 灵动交互窗口使用说明" not in text
    # 保留指向 skill 的指引
    assert "skill://minecraft" in text
    assert "skill://nimble-window" in text


def test_agent_md_explains_memory_system():
    """主提示词讲解记忆系统，并强调写入用户信息。"""
    text = (AGENT_TEMPLATE / "AGENT.md").read_text(encoding="utf-8")
    assert "memory://" in text
    assert "长期记忆" in text
    assert "用户偏好" in text
    assert "memory://user" in text


def test_faustbot_using_guide_skill_exists_and_has_meta():
    """faustbot-using-guide 内置 Skill：含架构/目录/文档站点链接。"""
    skill_dir = SKILL_TEMPLATE / "faustbot-using-guide"
    assert (skill_dir / "SKILL.md").exists()
    meta = json.loads((skill_dir / "_meta.json").read_text(encoding="utf-8"))
    assert meta["slug"] == "faustbot-using-guide"
    assert meta["builtin"] is True
    assert meta["description"].strip()
    assert meta["usage"].strip()

    text = (skill_dir / "SKILL.md").read_text(encoding="utf-8")
    # 架构介绍
    assert "FastAPI" in text
    assert "LangGraph" in text
    assert "13900" in text
    # ~/.faustbot 目录说明
    assert "~/.faustbot" in text
    assert "provider.private.json" in text
    assert "agents/faust" in text
    assert "faust.config.json" in text
    # 文档站点链接
    assert "faustbot.allenlee.xyz" in text
