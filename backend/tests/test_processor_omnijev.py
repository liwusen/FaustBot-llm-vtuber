"""OmniJev Processor 的配置归一、指纹与调用前处理（不加载真模型、不联网）。"""

from __future__ import annotations

import sys
from pathlib import Path

import numpy as np
import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.processors.builtin.omnijev import OmnijevProcessor, normalize_config


# ── 配置 ────────────────────────────────────────────────────


def test_normalize_config_defaults_and_override():
    assert normalize_config({}) == {"size": "4B", "quant": "4bit",
                                    "max_pixels": 768 * 28 * 28, "state_cache": 4}
    cfg = normalize_config({"size": "0.8B", "quant": "none", "max_pixels": 1000, "state_cache": 0})
    assert cfg == {"size": "0.8B", "quant": "none", "max_pixels": 1000, "state_cache": 0}
    assert normalize_config({"quant": None})["quant"] == "4bit"      # 未指定 → 默认档位
    assert normalize_config({"quant": ""})["quant"] == "none"        # 显式空串 → 不量化
    assert normalize_config({"quant": "off"})["quant"] == "none"


@pytest.mark.parametrize("bad", [
    {"size": "7B"},                 # 未知尺寸
    {"quant": "3bit"},              # 未知档位
    {"max_pixels": 10},             # 视觉 token 上限过小
    {"state_cache": -1},
    {"temperature": 0.5},           # 不认识的字段必须报错，不静默忽略
])
def test_normalize_config_rejects_bad_values(bad):
    with pytest.raises(ValueError):
        normalize_config(bad)


def test_setup_fingerprint_tracks_weights_not_runtime_knobs():
    proc = OmnijevProcessor()
    base = proc.setup_fingerprint({})
    assert proc.setup_fingerprint({"max_pixels": 4096, "state_cache": 1}) == base
    assert proc.setup_fingerprint({"size": "0.8B"}) != base
    assert proc.setup_fingerprint({"quant": "8bit"}) != base
    assert proc.setup_fingerprint({"quant": "none"}) != base


# ── 问题校验与选项补全 ───────────────────────────────────────


def test_check_question_accepts_three_types():
    proc = OmnijevProcessor()
    proc._check_question("q1", {"type": "noul", "instructions": "在下雨。"})
    proc._check_question("q2", {"type": "choice", "instructions": "哪个？", "criteria": {"a": None}})
    proc._check_question("q3", {"type": "score", "instructions": "多亮？", "levels": ["亮", "暗"]})


@pytest.mark.parametrize("bad", [
    {"type": "noul"},                                     # 缺 instructions
    {"instructions": "在做什么？", "criteria": {"a": None}},   # 缺 type
    {"type": "bbox", "instructions": "在哪？"},              # 未知问型
    {"type": "choice", "instructions": "哪个？"},            # 既无 criteria 也无 options
])
def test_check_question_rejects_bad_payload(bad):
    with pytest.raises(ValueError):
        OmnijevProcessor()._check_question("q", bad)


def test_prepare_questions_fills_option_keys():
    proc = OmnijevProcessor()
    prepared = proc._prepare_questions({
        "q": {"type": "choice", "instructions": "选一个", "options": [
            {"text": "带文本"}, {"key": "k1", "text": "带 key"}, {"key": "box", "region": {"box": [0, 0, 1, 1]}},
            "裸字符串", {}]},
    })
    options = prepared["q"]["options"]
    assert options[0] == {"text": "带文本"}       # 有 text 时上游能自己取 key，不额外造 key
    assert options[1]["key"] == "k1"
    assert options[2]["key"] == "box"             # region 选项没有 text，必须保 key
    assert options[3] == {"text": "裸字符串"}      # 裸字符串按 text 归一
    assert options[4]["key"] == "option_4"        # 既无 key 也无 text：给一个可辨识的 key


# ── 帧落盘（内容寻址 → 状态缓存可复用） ────────────────────────


def test_dump_frame_is_content_addressed_and_reused(tmp_path):
    proc = OmnijevProcessor()
    proc._dirs = {"frames": tmp_path / "frames"}
    frame = np.zeros((8, 6, 3), dtype=np.uint8)

    class _Ctx:
        def log(self, *_args, **_kwargs):
            pass

    first = proc._dump_frame(_Ctx(), frame, 0)
    stamp = Path(first).stat().st_mtime_ns
    assert Path(first).is_file()
    # 同样的画面必须命中同一个文件（否则 MSO1 的状态缓存会因 mtime 变化失效）
    assert proc._dump_frame(_Ctx(), frame.copy(), 0) == first
    assert Path(first).stat().st_mtime_ns == stamp
    # 不同画面 → 不同文件
    other = proc._dump_frame(_Ctx(), np.full((8, 6, 3), 255, dtype=np.uint8), 0)
    assert other != first


def test_dump_frame_rejects_bad_shape(tmp_path):
    proc = OmnijevProcessor()
    proc._dirs = {"frames": tmp_path}

    class _Ctx:
        def log(self, *_args, **_kwargs):
            pass

    for bad in (np.zeros((8, 6), dtype=np.uint8), np.zeros((8, 6, 3), dtype=np.float32)):
        with pytest.raises(ValueError):
            proc._dump_frame(_Ctx(), bad, 0)
