"""默认工作目录（WORKDIR_ROOT）测试。

所有工具的默认工作目录 = `~/.faustbot/agents/{AGENT_NAME}/`：
`load_configs()` 按当前 AGENT_NAME 重算 WORKDIR_ROOT 并确保目录存在，
execute / read / write / edit / find / search / nimble 在缺省参数下用的就是它。

Run: python -m pytest backend/tests/test_workdir_root.py -v
"""

from __future__ import annotations

import json
import os
import sys

import pytest

sys.path.insert(0, os.path.join(os.path.dirname(__file__), ".."))


@pytest.fixture
def config_env(tmp_path, monkeypatch):
    """把 config_loader 指向 tmp_path 下的最小配置，并在用例结束后还原全部大写全局量。"""
    import faust_backend.config_loader as conf

    snapshot = {k: v for k, v in vars(conf).items() if k.isupper()}
    cfg_path = tmp_path / "faust.config.json"
    priv_path = tmp_path / "faust.config.private.json"
    cfg_path.write_text(json.dumps({"AGENT_NAME": "demo_agent"}), encoding="utf-8")
    priv_path.write_text("{}", encoding="utf-8")
    monkeypatch.setattr(conf, "CONFIG_ROOT", str(tmp_path))
    monkeypatch.setattr(conf, "CONFIG_FILE_PATH", str(cfg_path))
    monkeypatch.setattr(conf, "CONFIG_FILE_P_PATH", str(priv_path))
    try:
        yield conf, tmp_path
    finally:
        for key, value in snapshot.items():
            setattr(conf, key, value)


def test_load_configs_workdir_is_agent_dir(config_env):
    conf, tmp_path = config_env

    conf.load_configs()

    expected = os.path.join(str(tmp_path), "agents", "demo_agent")
    assert conf.WORKDIR_ROOT == expected
    assert conf.AGENT_ROOT == expected
    assert os.path.isdir(expected)


def test_tools_default_workdir_follows_agent_name(config_env):
    """工具缺省参数解析出的工作目录就是 WORKDIR_ROOT（无参 → Agent 目录）。"""
    from faust_backend.tools.execute import _resolve_cwd

    conf, tmp_path = config_env
    conf.load_configs()
    assert _resolve_cwd("") == conf.WORKDIR_ROOT

    # 切换 Agent → WORKDIR_ROOT 跟随（reload_configs 走同一条 load_configs 路径）
    (tmp_path / "faust.config.json").write_text(
        json.dumps({"AGENT_NAME": "other_agent"}), encoding="utf-8"
    )
    conf.reload_configs()

    assert conf.WORKDIR_ROOT == os.path.join(str(tmp_path), "agents", "other_agent")
    assert _resolve_cwd("") == conf.WORKDIR_ROOT
    assert os.path.isdir(conf.WORKDIR_ROOT)


def test_explicit_cwd_wins(config_env):
    from faust_backend.tools.execute import _resolve_cwd

    assert _resolve_cwd("/tmp") == "/tmp"
