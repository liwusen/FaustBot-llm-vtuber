import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

import pytest

import faust_backend.vrm_pose_manager as vpm


@pytest.fixture(autouse=True)
def _isolate(tmp_path, monkeypatch):
    monkeypatch.setattr(vpm, "VRM_POSE_PATH", str(tmp_path / "vrm_poses.json"))
    yield


def test_default_empty():
    assert vpm.get_vrm_poses() == {}


def test_save_and_get_roundtrip():
    entry = vpm.save_vrm_pose("wave_hi", {"bones": {}, "transition": 500})
    assert entry["name"] == "wave_hi"
    assert vpm.get_vrm_poses()["wave_hi"]["pose"]["transition"] == 500


def test_save_overwrites():
    vpm.save_vrm_pose("p", {"transition": 100})
    vpm.save_vrm_pose("p", {"transition": 800})
    assert vpm.get_vrm_poses()["p"]["pose"]["transition"] == 800


def test_delete_existing():
    vpm.save_vrm_pose("p", {})
    assert vpm.delete_vrm_pose("p") is True
    assert "p" not in vpm.get_vrm_poses()


def test_delete_missing_returns_false():
    assert vpm.delete_vrm_pose("nope") is False


@pytest.mark.parametrize("bad", ["", "a b", "  spaced  ", "a\tb", "a\nb"])
def test_validate_rejects_whitespace(bad):
    assert vpm.validate_pose_name(bad) is not None


def test_validate_accepts_plain():
    assert vpm.validate_pose_name("wave_hi") is None
    assert vpm.validate_pose_name("pose-1") is None


def test_validate_rejects_non_string():
    assert vpm.validate_pose_name(123) is not None
    assert vpm.validate_pose_name(None) is not None
