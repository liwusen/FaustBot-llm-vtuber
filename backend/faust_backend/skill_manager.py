from __future__ import annotations

import datetime
import json
import re
import shutil
import tempfile
import zipfile
from pathlib import Path
from typing import Any

import requests

import faust_backend.config_loader as conf


_SAFE_SLUG = re.compile(r"^[a-zA-Z0-9][a-zA-Z0-9_\-]{0,63}$")


class SkillError(RuntimeError):
    pass


class SkillAlreadyInstalledError(SkillError):
    pass


def _agents_root() -> Path:
    return Path(conf.CONFIG_ROOT) / "agents"


def _resolve_agent(agent_name: str | None = None) -> str:
    target = str(agent_name or conf.AGENT_NAME or "").strip()
    if not target:
        raise SkillError("agent_name 不能为空")
    return target


def _agent_root(agent_name: str | None = None) -> Path:
    agent = _resolve_agent(agent_name)
    p = _agents_root() / agent
    p.mkdir(parents=True, exist_ok=True)
    return p


def _skill_dir(agent_name: str | None = None) -> Path:
    p = _agent_root(agent_name) / "skill.d"
    p.mkdir(parents=True, exist_ok=True)
    return p


def _state_file(agent_name: str | None = None) -> Path:
    return _skill_dir(agent_name) / "skills.state.json"


def _load_state(agent_name: str | None = None) -> dict[str, Any]:
    f = _state_file(agent_name)
    if not f.exists():
        return {"skills": {}}
    try:
        data = json.loads(f.read_text(encoding="utf-8"))
        if not isinstance(data, dict):
            return {"skills": {}}
        data.setdefault("skills", {})
        if not isinstance(data.get("skills"), dict):
            data["skills"] = {}
        return data
    except Exception:
        return {"skills": {}}


def _save_state(agent_name: str | None, state: dict[str, Any]) -> None:
    f = _state_file(agent_name)
    f.parent.mkdir(parents=True, exist_ok=True)
    f.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def _skill_paths(agent_name: str | None = None) -> list[Path]:
    root = _skill_dir(agent_name)
    return [p for p in sorted(root.iterdir()) if p.is_dir() and not p.name.startswith("_")]


def _read_skill_meta(skill_path: Path) -> dict[str, Any]:
    meta_file = skill_path / "_meta.json"
    if not meta_file.exists():
        return {"slug": skill_path.name, "version": "0.0.0"}
    try:
        data = json.loads(meta_file.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("slug", skill_path.name)
            return data
        return {"slug": skill_path.name, "version": "0.0.0"}
    except Exception:
        return {"slug": skill_path.name, "version": "0.0.0"}


def _find_skill_root(extract_dir: Path) -> Path:
    candidates = [p.parent for p in extract_dir.rglob("_meta.json") if p.is_file()]
    if not candidates:
        raise SkillError("skill 包中未找到 _meta.json")
    if len(candidates) == 1:
        return candidates[0]
    for c in candidates:
        if (c / "SKILL.md").exists():
            return c
    return candidates[0]


def _download_skill_zip(slug: str, target_zip: Path) -> str:
    url = f"https://wry-manatee-359.convex.site/api/v1/download?slug={requests.utils.quote(slug, safe='')}"
    resp = requests.get(url, timeout=60)
    resp.raise_for_status()
    target_zip.write_bytes(resp.content)
    return url


def _install_skill_root(
    skill_root: Path,
    *,
    agent_name: str | None = None,
    overwrite: bool = False,
    expected_slug: str | None = None,
    source: str = "",
) -> dict[str, Any]:
    if not (skill_root / "SKILL.md").exists():
        raise SkillError("skill 包缺少 SKILL.md")

    meta = _read_skill_meta(skill_root)
    meta_slug = str(meta.get("slug") or "").strip()
    if not _SAFE_SLUG.match(meta_slug):
        raise SkillError(f"_meta.json 中 slug 非法: {meta_slug}")

    if expected_slug and meta_slug != expected_slug:
        raise SkillError(f"slug 不匹配: 请求={expected_slug}, 包内={meta_slug}")

    agent = _resolve_agent(agent_name)
    target_dir = _skill_dir(agent) / meta_slug
    if target_dir.exists() and not overwrite:
        raise SkillAlreadyInstalledError(f"Skill 已存在: {meta_slug}")

    if target_dir.exists():
        shutil.rmtree(target_dir)
    shutil.copytree(skill_root, target_dir)

    state = _load_state(agent)
    info = {
        "slug": meta_slug,
        "version": str(meta.get("version") or "0.0.0"),
        "enabled": True,
        "installed_at": datetime.datetime.now().isoformat(),
        "source": source,
        "path": str(target_dir.resolve()),
    }
    state.setdefault("skills", {})[meta_slug] = info
    _save_state(agent, state)
    return {"agent": agent, **info}


def install_skill(slug: str, *, agent_name: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    skill_slug = str(slug or "").strip()
    if not _SAFE_SLUG.match(skill_slug):
        raise SkillError(f"非法 skill slug: {skill_slug}")

    with tempfile.TemporaryDirectory(prefix="faust-skill-") as td:
        tmp = Path(td)
        zip_path = tmp / "skill.zip"
        extract_dir = tmp / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        source_url = _download_skill_zip(skill_slug, zip_path)
        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise SkillError("下载结果不是有效 ZIP") from exc

        skill_root = _find_skill_root(extract_dir)
        return _install_skill_root(
            skill_root,
            agent_name=agent_name,
            overwrite=overwrite,
            expected_slug=skill_slug,
            source=source_url,
        )


def install_skill_from_zip(zip_path: str, *, agent_name: str | None = None, overwrite: bool = False) -> dict[str, Any]:
    path_text = str(zip_path or "").strip()
    if not path_text:
        raise SkillError("zip_path 不能为空")

    src = Path(path_text).expanduser().resolve()
    if not src.exists() or not src.is_file():
        raise SkillError(f"ZIP 文件不存在: {src}")

    with tempfile.TemporaryDirectory(prefix="faust-skill-local-") as td:
        extract_dir = Path(td) / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)
        try:
            with zipfile.ZipFile(src, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise SkillError("本地文件不是有效 ZIP") from exc

        skill_root = _find_skill_root(extract_dir)
        return _install_skill_root(
            skill_root,
            agent_name=agent_name,
            overwrite=overwrite,
            source=f"file://{src.as_posix()}",
        )


def list_skills(*, agent_name: str | None = None) -> list[dict[str, Any]]:
    agent = _resolve_agent(agent_name)
    state = _load_state(agent)
    skill_state = state.get("skills") or {}

    out: list[dict[str, Any]] = []
    for p in _skill_paths(agent):
        slug = p.name
        meta = _read_skill_meta(p)
        st = skill_state.get(slug) or {}
        out.append(
            {
                "slug": slug,
                "version": str(meta.get("version") or st.get("version") or "0.0.0"),
                "enabled": bool(st.get("enabled", True)),
                "installed_at": st.get("installed_at"),
                "source": st.get("source"),
                "path": str(p.resolve()),
            }
        )

    # state 中存在但目录不在（脏数据）时也返回，便于修复
    dir_slugs = {x["slug"] for x in out}
    for slug, st in skill_state.items():
        if slug in dir_slugs:
            continue
        out.append(
            {
                "slug": slug,
                "version": str(st.get("version") or "0.0.0"),
                "enabled": bool(st.get("enabled", True)),
                "installed_at": st.get("installed_at"),
                "source": st.get("source"),
                "path": st.get("path"),
                "missing": True,
            }
        )

    return sorted(out, key=lambda x: str(x.get("slug") or ""))


def get_skill_detail(slug: str, *, agent_name: str | None = None) -> dict[str, Any]:
    skill_slug = str(slug or "").strip()
    if not _SAFE_SLUG.match(skill_slug):
        raise SkillError(f"非法 skill slug: {skill_slug}")

    agent = _resolve_agent(agent_name)
    p = _skill_dir(agent) / skill_slug
    if not p.exists() or not p.is_dir():
        raise SkillError(f"Skill 不存在: {skill_slug}")

    state = _load_state(agent).get("skills") or {}
    st = state.get(skill_slug) or {}
    meta = _read_skill_meta(p)
    skill_md = ""
    skill_md_path = p / "SKILL.md"
    if skill_md_path.exists():
        try:
            skill_md = skill_md_path.read_text(encoding="utf-8")
        except Exception:
            skill_md = ""

    files = [str(x.relative_to(p)).replace("\\", "/") for x in p.rglob("*") if x.is_file()]
    return {
        "slug": skill_slug,
        "meta": meta,
        "enabled": bool(st.get("enabled", True)),
        "installed_at": st.get("installed_at"),
        "source": st.get("source"),
        "path": str(p.resolve()),
        "skill_md": skill_md,
        "files": files,
    }


def remove_skill(slug: str, *, agent_name: str | None = None) -> dict[str, Any]:
    skill_slug = str(slug or "").strip()
    if not _SAFE_SLUG.match(skill_slug):
        raise SkillError(f"非法 skill slug: {skill_slug}")

    agent = _resolve_agent(agent_name)
    p = _skill_dir(agent) / skill_slug
    if not p.exists() or not p.is_dir():
        raise SkillError(f"Skill 不存在: {skill_slug}")

    shutil.rmtree(p)
    state = _load_state(agent)
    state.setdefault("skills", {}).pop(skill_slug, None)
    _save_state(agent, state)
    return {"agent": agent, "slug": skill_slug, "deleted": True}


def set_skill_enabled(slug: str, enabled: bool, *, agent_name: str | None = None) -> dict[str, Any]:
    skill_slug = str(slug or "").strip()
    if not _SAFE_SLUG.match(skill_slug):
        raise SkillError(f"非法 skill slug: {skill_slug}")

    agent = _resolve_agent(agent_name)
    p = _skill_dir(agent) / skill_slug
    if not p.exists() or not p.is_dir():
        raise SkillError(f"Skill 不存在: {skill_slug}")

    state = _load_state(agent)
    item = state.setdefault("skills", {}).setdefault(skill_slug, {})
    item["enabled"] = bool(enabled)
    item.setdefault("version", str(_read_skill_meta(p).get("version") or "0.0.0"))
    item.setdefault("path", str(p.resolve()))
    item.setdefault("installed_at", datetime.datetime.now().isoformat())
    _save_state(agent, state)
    return {
        "agent": agent,
        "slug": skill_slug,
        "enabled": bool(enabled),
    }


def _builtin_skill_dir() -> Path:
    """Return the project-level built-in skills directory.

    Uses ``conf.PROJECT_ROOT / "agents" / "skill.d"``.
    """
    return Path(conf.PROJECT_ROOT) / "agents" / "skill.d"


def list_skills_yaml(agent_name: str | None = None) -> str:
    """Return a YAML string describing enabled MCP skills for the given agent.

    Each entry includes ``slug``, ``summary`` (from _meta.json description or
    first 2 lines of SKILL.md), and ``usage`` (from _meta.json).  Only skills
    whose state entry has ``enabled: true`` (or is absent from state) are
    included.  Returns an empty string when there are no enabled skills.
    """
    agent = _resolve_agent(agent_name)
    state = _load_state(agent)
    skill_state = state.get("skills") or {}

    entries: list[dict[str, str]] = []
    for p in _skill_paths(agent):
        slug = p.name
        st = skill_state.get(slug) or {}
        if not st.get("enabled", True):
            continue

        meta = _read_skill_meta(p)
        summary = (meta.get("description") or "").strip()
        if not summary:
            skill_md = p / "SKILL.md"
            if skill_md.exists():
                try:
                    raw = skill_md.read_text(encoding="utf-8").splitlines()
                    non_empty = [l for l in raw if l.strip()]
                    summary = "\n".join(non_empty[:2]).strip()
                except Exception:
                    summary = ""

        usage = (meta.get("usage") or "").strip()

        entries.append({"slug": slug, "summary": summary, "usage": usage})

    if not entries:
        return ""

    try:
        import yaml as _yaml

        return _yaml.dump(
            {"mcp_skills": entries},
            allow_unicode=True,
            default_flow_style=False,
            sort_keys=False,
        )
    except ImportError:
        lines = ["mcp_skills:"]
        for e in entries:
            slug = e["slug"]
            summary = e["summary"].replace("\\", "\\\\").replace('"', '\\"')
            usage = e["usage"].replace("\\", "\\\\").replace('"', '\\"')
            lines.append(f"  - slug: {slug}")
            lines.append(f'    summary: "{summary}"')
            lines.append(f'    usage: "{usage}"')
        return "\n".join(lines) + "\n"


def _ensure_builtin_skills(agent_name: str | None = None) -> None:
    """Copy project-level built-in skills to the user agent's skill directory.

    For each sub-directory under ``_builtin_skill_dir()`` that does not yet
    exist in the user skill directory, the directory is copied and
    ``builtin: true`` / ``enabled: true`` is persisted in the skill state.

    Safe to call multiple times (idempotent).  Does nothing if the
    project-level built-in skills directory does not exist.
    """
    builtin_dir = _builtin_skill_dir()
    if not builtin_dir.exists() or not builtin_dir.is_dir():
        return

    agent = _resolve_agent(agent_name)
    user_skill_dir = _skill_dir(agent)
    state = _load_state(agent)

    for p in sorted(builtin_dir.iterdir()):
        if not p.is_dir() or p.name.startswith("_"):
            continue
        slug = p.name
        target = user_skill_dir / slug
        if not target.exists():
            shutil.copytree(p, target)

        st = state.setdefault("skills", {}).setdefault(slug, {})
        st["builtin"] = True
        st["enabled"] = True
        st.setdefault("version", str(_read_skill_meta(p).get("version") or "0.0.0"))
        st.setdefault("path", str(target.resolve()))

    _save_state(agent, state)
