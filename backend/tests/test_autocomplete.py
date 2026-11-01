from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.api.system.autocomplete import autocomplete


def _labels(payload: dict) -> list[str]:
    result = asyncio.run(autocomplete(payload))
    return [str(item.get("label") or "") for item in result.get("items") or []]


def test_vng_command_listed():
    labels = _labels({"text": "/", "cursor": 1})
    assert "vng" in labels
    assert "vng off" in labels


def test_vng_prefix_keeps_off_option_after_space():
    labels = _labels({"text": "/vng ", "cursor": 5})
    assert "vng" in labels
    assert "vng off" in labels


def test_vng_off_filters_out_plain_vng():
    labels = _labels({"text": "/vng o", "cursor": 6})
    assert "vng off" in labels
    assert "vng" not in labels
