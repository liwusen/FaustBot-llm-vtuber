import os
from fastapi import APIRouter, HTTPException
import faust_backend.skill_manager as skill_manager
from faust_backend.runtime import state

router = APIRouter(tags=["admin-skills"])
router.description = "Skill 管理：列出/查看/安装（市场/ZIP）/更新 SKILL.md/删除/启用/禁用 Skill"


@router.get("/faust/admin/skills")
async def admin_list_skills(agent_name: str | None = None):
    try:
        items = skill_manager.list_skills(agent_name=agent_name)
        return {"status": "ok", "agent": agent_name or state.AGENT_NAME, "items": items}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 列表读取失败: {e}")


@router.get("/faust/admin/skills/{slug}")
async def admin_get_skill_detail(slug: str, agent_name: str | None = None):
    try:
        detail = skill_manager.get_skill_detail(slug, agent_name=agent_name)
        return {"status": "ok", "agent": agent_name or state.AGENT_NAME, "detail": detail}
    except Exception as e:
        raise HTTPException(status_code=404, detail=f"Skill 详情读取失败: {e}")


@router.put("/faust/admin/skills/{slug}/skill-md")
async def admin_update_skill_md(slug: str, payload: dict | None = None):
    body = payload or {}
    agent_name = body.get("agent_name")
    content = str(body.get("content") or "")
    try:
        detail = skill_manager.get_skill_detail(slug, agent_name=agent_name)
        skill_path = str(detail.get("path") or "").strip()
        if not skill_path:
            raise RuntimeError("Skill 路径为空")
        md_path = os.path.join(skill_path, "SKILL.md")
        with open(md_path, "w", encoding="utf-8") as f:
            f.write(content)
        refreshed = skill_manager.get_skill_detail(slug, agent_name=agent_name)
        return {"status": "ok", "detail": refreshed}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"SKILL.md 保存失败: {e}")


@router.post("/faust/admin/skills/install")
async def admin_install_skill(payload: dict | None = None):
    body = payload or {}
    slug = str(body.get("slug") or "").strip()
    agent_name = body.get("agent_name")
    overwrite = bool(body.get("overwrite", False))
    if not slug:
        raise HTTPException(status_code=400, detail="缺少 slug")
    try:
        item = skill_manager.install_skill(slug, agent_name=agent_name, overwrite=overwrite)
        return {"status": "ok", "item": item}
    except skill_manager.SkillAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 安装失败: {e}")


@router.post("/faust/admin/skills/install-zip")
async def admin_install_skill_from_zip(payload: dict | None = None):
    body = payload or {}
    zip_path = str(body.get("zip_path") or "").strip()
    agent_name = body.get("agent_name")
    overwrite = bool(body.get("overwrite", False))
    if not zip_path:
        raise HTTPException(status_code=400, detail="缺少 zip_path")
    try:
        item = skill_manager.install_skill_from_zip(zip_path, agent_name=agent_name, overwrite=overwrite)
        return {"status": "ok", "item": item}
    except skill_manager.SkillAlreadyInstalledError as e:
        raise HTTPException(status_code=409, detail=str(e))
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill ZIP 安装失败: {e}")


@router.delete("/faust/admin/skills/{slug}")
async def admin_delete_skill(slug: str, agent_name: str | None = None):
    try:
        result = skill_manager.remove_skill(slug, agent_name=agent_name)
        return {"status": "ok", "deleted": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 删除失败: {e}")


@router.post("/faust/admin/skills/{slug}/enable")
async def admin_enable_skill(slug: str, payload: dict | None = None):
    agent_name = (payload or {}).get("agent_name")
    try:
        result = skill_manager.set_skill_enabled(slug, True, agent_name=agent_name)
        return {"status": "ok", "item": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 启用失败: {e}")


@router.post("/faust/admin/skills/{slug}/disable")
async def admin_disable_skill(slug: str, payload: dict | None = None):
    agent_name = (payload or {}).get("agent_name")
    try:
        result = skill_manager.set_skill_enabled(slug, False, agent_name=agent_name)
        return {"status": "ok", "item": result}
    except Exception as e:
        raise HTTPException(status_code=400, detail=f"Skill 禁用失败: {e}")
