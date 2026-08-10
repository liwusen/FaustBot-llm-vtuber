import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.skill_manager import _skill_paths


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
