import json
import os
from os.path import join as pjoin

import faust_backend.config_loader as conf

VRM_POSE_PATH = pjoin(conf.CONFIG_ROOT, "vrm_poses.json")


def _load() -> dict:
    if not os.path.exists(VRM_POSE_PATH):
        return {}
    try:
        with open(VRM_POSE_PATH, encoding="utf-8") as f:
            data = json.load(f)
        return data if isinstance(data, dict) else {}
    except (json.JSONDecodeError, OSError):
        return {}


def _persist(poses: dict) -> None:
    with open(VRM_POSE_PATH, "w", encoding="utf-8") as f:
        json.dump(poses, f, ensure_ascii=False, indent=4)


def validate_pose_name(name) -> str | None:
    """Return an error message if `name` is not a valid preset name, else None."""
    if not isinstance(name, str) or not name.strip():
        return "preset name must be a non-empty string"
    if name != name.strip() or any(ch.isspace() for ch in name):
        return "preset name must not contain whitespace"
    return None


def get_vrm_poses() -> dict:
    return _load()


def save_vrm_pose(name, pose: dict) -> dict:
    err = validate_pose_name(name)
    if err:
        raise ValueError(err)
    poses = _load()
    entry = {"name": name, "pose": pose if isinstance(pose, dict) else {}}
    poses[name] = entry
    _persist(poses)
    return entry


def delete_vrm_pose(name) -> bool:
    poses = _load()
    if name not in poses:
        return False
    del poses[name]
    _persist(poses)
    return True
