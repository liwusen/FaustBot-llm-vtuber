import sys
import json
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.skill_manager import _skill_paths
from faust_backend import skill_manager


def test_skill_paths_only_scans_agent_skill_dir(tmp_path, monkeypatch):
    """只识别 agents/<name>/skill.d 下的目录；~/.agents/skills 不再纳入。"""
    agent_dir = tmp_path / "agent"
    (agent_dir / "skill.d" / "minecraft").mkdir(parents=True)
    (agent_dir / "skill.d" / "csv").mkdir(parents=True)
    # 非技能文件/隐藏目录不应出现
    (agent_dir / "skill.d" / "skills.state.json").write_text("{}", encoding="utf-8")
    (agent_dir / "skill.d" / "_private").mkdir()
    (agent_dir / "skill.d" / ".hidden").mkdir()

    monkeypatch.setattr("faust_backend.skill_manager._skill_dir", lambda agent_name=None: agent_dir / "skill.d")

    names = sorted(p.name for p in _skill_paths("faust"))
    assert names == ["csv", "minecraft"]
    assert "skills" not in names  # 全局技能根/隐藏目录绝不出现


def _make_skill(root: Path, slug: str, version: str, marker: str) -> None:
    d = root / slug
    d.mkdir(parents=True, exist_ok=True)
    (d / "_meta.json").write_text(json.dumps({"slug": slug, "version": version}), encoding="utf-8")
    (d / "SKILL.md").write_text(marker, encoding="utf-8")


def test_ensure_builtin_skills_force_updates_on_newer_template(tmp_path, monkeypatch):
    tpl = tmp_path / "tpl"
    agent_skills = tmp_path / "agent" / "skill.d"
    _make_skill(tpl, "demo", "1.1.0", "new-content")
    _make_skill(agent_skills, "demo", "1.0.0", "old-content")

    monkeypatch.setattr(skill_manager, "_builtin_skill_dir", lambda: tpl)
    monkeypatch.setattr(skill_manager, "_skill_dir", lambda agent_name=None: agent_skills)
    monkeypatch.setattr(skill_manager, "_load_state", lambda agent_name=None: {})
    saved: dict = {}
    monkeypatch.setattr(skill_manager, "_save_state", lambda agent_name, state: saved.update(state))

    skill_manager._ensure_builtin_skills()

    assert (agent_skills / "demo" / "SKILL.md").read_text(encoding="utf-8") == "new-content"
    assert saved["skills"]["demo"]["version"] == "1.1.0"
    assert saved["skills"]["demo"]["builtin"] is True


def test_ensure_builtin_skills_keeps_same_version(tmp_path, monkeypatch):
    tpl = tmp_path / "tpl"
    agent_skills = tmp_path / "agent" / "skill.d"
    _make_skill(tpl, "demo", "1.0.0", "new-content")
    _make_skill(agent_skills, "demo", "1.0.0", "local-edits")

    monkeypatch.setattr(skill_manager, "_builtin_skill_dir", lambda: tpl)
    monkeypatch.setattr(skill_manager, "_skill_dir", lambda agent_name=None: agent_skills)
    monkeypatch.setattr(skill_manager, "_load_state", lambda agent_name=None: {})
    monkeypatch.setattr(skill_manager, "_save_state", lambda agent_name, state: None)

    skill_manager._ensure_builtin_skills()

    # 同版本不覆盖(保留本地修改)
    assert (agent_skills / "demo" / "SKILL.md").read_text(encoding="utf-8") == "local-edits"


def test_ensure_builtin_skills_installs_missing(tmp_path, monkeypatch):
    tpl = tmp_path / "tpl"
    agent_skills = tmp_path / "agent" / "skill.d"
    _make_skill(tpl, "fresh", "2.0.0", "hello")

    monkeypatch.setattr(skill_manager, "_builtin_skill_dir", lambda: tpl)
    monkeypatch.setattr(skill_manager, "_skill_dir", lambda agent_name=None: agent_skills)
    monkeypatch.setattr(skill_manager, "_load_state", lambda agent_name=None: {})
    saved: dict = {}
    monkeypatch.setattr(skill_manager, "_save_state", lambda agent_name, state: saved.update(state))

    skill_manager._ensure_builtin_skills()

    assert (agent_skills / "fresh" / "SKILL.md").read_text(encoding="utf-8") == "hello"
    assert saved["skills"]["fresh"]["version"] == "2.0.0"
