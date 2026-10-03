import asyncio
import json
import tempfile
from pathlib import Path

from fastapi.encoders import jsonable_encoder

from faust_backend.runtime import state
from faust_backend.routes import debugging
from faust_backend.subagent_manager import SubagentManager
import faust_backend.skill_manager as skill_manager
import faust_backend.config_loader as conf


async def case(label, setup, teardown):
    setup()
    try:
        payload = await debugging.snapshot()
        json.dumps(jsonable_encoder(payload))  # 模拟 FastAPI 响应渲染
        print(f"[{label}] -> NO ERROR (200)")
    except Exception as exc:  # noqa: BLE001
        print(f"[{label}] -> {type(exc).__name__}: {exc}")
    finally:
        teardown()


async def main() -> None:
    real_sm = SubagentManager()
    saved_sm = state.subagent_manager
    saved_pm = state.plugin_manager
    saved_name = getattr(conf, "AGENT_NAME", "faust")
    saved_root = debugging.CONFIG_ROOT

    from faust_backend.plugin_system.manager import PluginManager

    real_pm = PluginManager()
    state.plugin_manager = real_pm
    state.subagent_manager = real_sm
    await case("baseline (real subagent_manager)", lambda: None, lambda: None)

    state.subagent_manager = None
    await case(
        "C1 subagent_manager=None",
        lambda: None,
        lambda: setattr(state, "subagent_manager", real_sm),
    )

    await case(
        "C2 plugin_manager=None",
        lambda: setattr(state, "plugin_manager", None),
        lambda: setattr(state, "plugin_manager", saved_pm),
    )

    tmp = Path(tempfile.mkdtemp(prefix="faust-cfg-"))
    await case(
        "C3 faust.config.json missing",
        lambda: setattr(debugging, "CONFIG_ROOT", str(tmp)),
        lambda: setattr(debugging, "CONFIG_ROOT", saved_root),
    )

    await case(
        "C4 AGENT_NAME empty",
        lambda: setattr(skill_manager.conf, "AGENT_NAME", ""),
        lambda: setattr(skill_manager.conf, "AGENT_NAME", saved_name),
    )

    holder = {}

    def setup_c5():
        class FakePM:
            def list_plugins(self):
                return [{"id": "x", "config": {"values": {"p": object()}}}]

        holder["pm"] = state.plugin_manager
        state.plugin_manager = FakePM()

    await case(
        "C5 non-JSON-serializable plugin config",
        setup_c5,
        lambda: setattr(state, "plugin_manager", holder["pm"]),
    )

    state.subagent_manager = saved_sm
    state.plugin_manager = saved_pm


asyncio.run(main())
