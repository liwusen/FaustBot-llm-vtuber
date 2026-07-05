import os
import faust_backend.skill_manager as skill_manager
from faust_backend.runtime import state
from faust_backend.logger import get_logger
from fastapi import APIRouter


log = get_logger("faust.autocomplete")

router = APIRouter(tags=["autocomplete"])
router.description = "斜杠命令自动补全：/skill:<slug> 等"


@router.post("/faust/autocomplete")
async def autocomplete(payload: dict):
    text: str = str(payload.get("text") or "").strip()
    cursor: int = int(payload.get("cursor") or len(text))

    log.info("autocomplete text=%r cursor=%d agent=%r", text, cursor, state.AGENT_NAME)

    if not text.startswith("/"):
        return {"items": []}

    cmd_text = text[1:cursor].lstrip()
    items: list[dict] = []
    
    # ── Skill autocomplete ──
    # Show /skill: command when user types just `/`, then filter skills after `skill:`
    if not cmd_text:
        # Just `/` typed — show /skill: as a command option
        items.append({
            "type": "command",
            "label": "skill:",
            "detail": "查看所有可用技能，选择后输入 skill 名称过滤",
            "insert_text": "/skill:",
            "cursor_offset": len("/skill:"),
        })
    elif cmd_text.lower().startswith("skill"):
        prefix = ""
        if ":" in cmd_text:
            parts = cmd_text.split(":", 1)
            prefix = parts[1].strip() if len(parts) > 1 else ""

        try:
            skill_manager._ensure_builtin_skills()
            skills = skill_manager.list_skills(agent_name=state.AGENT_NAME)
            log.info("autocomplete list_skills count=%d agent=%s", len(skills), state.AGENT_NAME)
            for s in skills:
                slug = str(s.get("slug") or "")
                if not s.get("enabled", True):
                    continue
                if prefix and prefix not in slug:
                    continue
                label = slug
                detail = str(s.get("description") or _read_skill_summary(slug) or "")
                items.append({
                    "type": "skill",
                    "label": label,
                    "detail": detail,
                    "insert_text": f"/skill:{slug} ",
                    "cursor_offset": len(f"/skill:{slug} "),
                })
            log.info("autocomplete result items=%d", len(items))
        except Exception as e:
            log.warning("Skill autocomplete error: %s", e)
    return {"items": items}


def _read_skill_summary(slug: str) -> str:
    """Read first meaningful line from SKILL.md as fallback summary."""
    try:
        detail = skill_manager.get_skill_detail(slug, agent_name=state.AGENT_NAME)
        skill_path = detail.get("path") or ""
        if not skill_path:
            return ""
        skill_md = os.path.join(skill_path, "SKILL.md")
        if not os.path.exists(skill_md):
            return ""
        with open(skill_md, "r", encoding="utf-8") as f:
            for line in f:
                stripped = line.strip()
                if stripped and not stripped.startswith("#"):
                    return stripped[:120]
        return ""
    except Exception:
        return ""
