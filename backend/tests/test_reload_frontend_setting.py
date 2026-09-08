from __future__ import annotations

import asyncio
import sys
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

from faust_backend.frontend.bridge import FrontendBridge


def test_bridge_reload_settings_pushes_command():
    bridge = FrontendBridge()

    async def _collect():
        bridge.reload_settings()
        return await asyncio.wait_for(bridge.queue.get(), timeout=1)

    assert asyncio.run(_collect()) == "RELOAD_FRONTEND_SETTING"
