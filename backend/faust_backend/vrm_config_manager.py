import os
import json
from os.path import join as pjoin
import faust_backend.config_loader as conf

VRM_CONFIG_DEFAULTS = {
    "arms": {
        "rightUpperArm": {"x": 0.1, "z": -1.2},
        "rightLowerArm": {"x": 0.7},
        "leftUpperArm": {"x": 0.1, "z": 1.2},
        "leftLowerArm": {"x": 0.7},
        "swingAmplitude": 0.06,
        "swingSpeed": 0.8,
    },
    "body": {
        "spineSwayX": 0.006,
        "spineSwayZ": 0.004,
        "swaySpeed": 0.7,
    },
    "head": {
        "neckZ": 0.008,
        "neckY": 0.006,
        "speed": 0.3,
    },
    "blink": {
        "minInterval": 2,
        "maxInterval": 4,
        "closeDuration": 0.08,
        "openDuration": 0.12,
    },
    "eye": {
        "saccadeRangeX": 0.4,
        "saccadeRangeY": 0.2,
        "minInterval": 3,
        "maxInterval": 6,
        "duration": 0.8,
        "mouseFovScale": 0.3,
        "mouseIdleTimeout": 8,
    },
    "microExp": {
        "minInterval": 8,
        "maxInterval": 12,
        "weight": 0.12,
        "fadeIn": 0.5,
        "hold": 1.5,
        "fadeOut": 0.5,
    },
    "modelState": {
        "positionX": 0,
        "positionY": 0,
        "rotation": 0,
        "scale": 0.5,
    },
}

VRM_CONFIG_PATH = pjoin(conf.CONFIG_ROOT, 'vrm_config.json')


def _deep_merge(defaults, overrides):
    out = {}
    for k, v in defaults.items():
        if isinstance(v, dict) and k in overrides and isinstance(overrides[k], dict):
            out[k] = _deep_merge(v, overrides[k])
        elif k in overrides:
            out[k] = overrides[k]
        else:
            out[k] = v
    for k, v in overrides.items():
        if k not in out:
            out[k] = v
    return out


def get_vrm_config():
    if not os.path.exists(VRM_CONFIG_PATH):
        return json.loads(json.dumps(VRM_CONFIG_DEFAULTS))
    try:
        with open(VRM_CONFIG_PATH, 'r', encoding='utf-8') as f:
            saved = json.load(f)
        return _deep_merge(VRM_CONFIG_DEFAULTS, saved)
    except (json.JSONDecodeError, IOError):
        return json.loads(json.dumps(VRM_CONFIG_DEFAULTS))


def save_vrm_config(config):
    merged = _deep_merge(VRM_CONFIG_DEFAULTS, config)
    with open(VRM_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(merged, f, ensure_ascii=False, indent=4)
    return merged


def save_vrm_model_state(state):
    current = get_vrm_config()
    if "modelState" not in current:
        current["modelState"] = {}
    for k, v in state.items():
        current["modelState"][k] = v
    with open(VRM_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(current, f, ensure_ascii=False, indent=4)
    return current["modelState"]


def reset_vrm_config():
    with open(VRM_CONFIG_PATH, 'w', encoding='utf-8') as f:
        json.dump(VRM_CONFIG_DEFAULTS, f, ensure_ascii=False, indent=4)
    return json.loads(json.dumps(VRM_CONFIG_DEFAULTS))
