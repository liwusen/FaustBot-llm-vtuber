"""LimitedUI 单测：假决策器 + 假 win/input/frontend 层（不碰真桌面、不触发真模型）。"""
from __future__ import annotations

import asyncio
import json
import sys
import tempfile
from pathlib import Path

import numpy as np
import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))
AGILE_DIR = BACKEND_ROOT / "default_plugins" / "agile-engine"
if str(AGILE_DIR) not in sys.path:
    sys.path.insert(0, str(AGILE_DIR))

import uidrv  # noqa: E402
from uidrv.audit import AuditWriter  # noqa: E402
from uidrv.decider import (  # noqa: E402
    DRY_RUN_SECONDS, Frame, HOOK_CONTEXT_MAX_CHARS, STALE_PHASH_MAX_DISTANCE, SessionSpec,
    SessionState, SpecError, UIDeps, perceptual_hash,
)
from uidrv.omnijev import OmniJevDecider  # noqa: E402
from uidrv.scripted import ScriptedDecider, synthetic_frame  # noqa: E402
from uidrv.win import TargetSpec, WindowInfo  # noqa: E402

HWND = 0x4242
TARGET_RECT = (100, 50, 900, 650)
OTHER_HWND = 0x99


def make_spec(**over) -> SessionSpec:
    raw = {
        "purpose": "自动打牌：每回合问 OmniJev 该出哪张牌",
        "target": {"exe": "fake.exe", "title_contains": "Fake"},
        "states": {
            "battle": {"describe": "战斗中：能看到双方血量与手牌",
                       "actions": {"1": "打出第1张牌", "space": "结束回合",
                                   "click_hand": {"click": [100, 800, 900, 950],
                                                  "meaning": "点手牌区中央"}}},
            "menu": {"describe": "菜单：竖排按钮列表",
                     "actions": {"space": "确认", "esc": "返回"}},
        },
        "blink": {"enabled": True, "settle_ms": 0, "ack_timeout_ms": 300},
        "limits": {"keys_per_sec": 4, "max_actions": 100, "max_session_s": 600,
                   "hook_timeout_s": 2.0},
    }
    raw.update(over)
    return SessionSpec.parse(raw)


def make_frame(seed: int = 0, *, pattern: str = "gradient", rect=TARGET_RECT) -> Frame:
    if pattern == "noise":
        arr = np.random.default_rng(seed).integers(0, 256, size=(120, 160, 3), dtype="uint8")
    else:
        arr = synthetic_frame(160, 120, seed)
    return Frame(image=arr, digest=f"{seed:04d}{pattern[:2]}", phash=perceptual_hash(arr),
                 rect=rect, captured_at=0.0)


class Clock:
    def __init__(self) -> None:
        self.t = 1000.0

    def __call__(self) -> float:
        return self.t

    def advance(self, seconds: float) -> None:
        self.t += seconds


class FakeWin:
    def __init__(self) -> None:
        self.hwnd = HWND
        self.rect = TARGET_RECT
        self.foreground = HWND
        self.alive = True
        self.root_at: dict[tuple[int, int], int] = {}

    def resolve(self, target: TargetSpec) -> WindowInfo:
        return WindowInfo(hwnd=self.hwnd, title="Fake Game", exe="fake.exe", pid=777,
                          path="C:/fake.exe", client_rect=self.rect)

    def client_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return self.rect

    def foreground_hwnd(self) -> int:
        return self.foreground

    def root_window_at(self, x: int, y: int) -> int:
        return self.root_at.get((x, y), self.hwnd)

    def is_window(self, hwnd: int) -> bool:
        return self.alive

    def is_minimized(self, hwnd: int) -> bool:
        return False

    def set_foreground(self, hwnd: int) -> bool:
        self.foreground = hwnd
        return True

    def screen_size(self) -> tuple[int, int]:
        return (2560, 1600)

    def virtual_screen(self) -> tuple[int, int, int, int]:
        return (0, 0, 2560, 1600)

    def monitor_rect(self, hwnd: int) -> tuple[int, int, int, int]:
        return (0, 0, 2560, 1600)


class FakeInput:
    def __init__(self) -> None:
        self.taps: list[str] = []
        self.clicks: list[tuple[int, int]] = []
        self._down: list[str] = []
        self.cursor: tuple[int, int] = (1280, 800)      # 屏幕中央，远离角落急停点

    def key_down(self, key: str) -> None:
        if key not in self._down:
            self._down.append(key)

    def key_up(self, key: str) -> None:
        if key in self._down:
            self._down.remove(key)

    def click(self, x: int, y: int) -> None:
        self.clicks.append((x, y))

    def release_all(self) -> list[str]:
        released = list(self._down)
        self._down.clear()
        return released

    def pressed(self) -> list[str]:
        return list(self._down)

    def cursor_pos(self) -> tuple[int, int]:
        return self.cursor

    async def key_tap(self, key: str, hold_ms: int = 30) -> None:
        self.taps.append(key)


class FakeFrontend:
    """默认窗口与目标客户区重叠（1000,100)-(1500,400) 不重叠；这里用重叠的坐标。"""

    def __init__(self, *, bounds=(200, 100, 700, 500), blink_ok=True, session_ok=True) -> None:
        self.bounds = bounds
        self.blink_ok = blink_ok
        self.session_ok = session_ok
        self.blinks: list[bool] = []
        self.hints: list[tuple[str, str, str]] = []
        self.sessions: list[bool] = []

    async def blink(self, hide: bool, timeout: float) -> bool:
        self.blinks.append(hide)
        return self.blink_ok

    async def hint(self, state: str, title: str, text: str) -> None:
        self.hints.append((state, title, text))

    async def window_bounds(self, timeout: float):
        return self.bounds

    async def set_session(self, active: bool, timeout: float = 1.0):
        self.sessions.append(active)
        if not self.session_ok:
            return None
        return {"active": active}


class Harness:
    """一组假依赖 + 决策器。"""

    def __init__(self, spec: SessionSpec | None = None, answers=None, *, cls=ScriptedDecider,
                 clock=None, win=None, inp=None, frontend=None, hil=None, hub=None,
                 audit_root=None, processor_ask=None, hooks=None):
        self._tmp = tempfile.TemporaryDirectory()
        self.spec = spec or make_spec()
        self.clock = clock or Clock()
        self.win = win or FakeWin()
        self.input = inp or FakeInput()
        self.frontend = frontend if frontend is not None else FakeFrontend()
        self.hil = hil
        self.escalations: list[tuple[dict, str]] = []

        async def _escalate(data: dict, recall: str) -> None:
            self.escalations.append((data, recall))

        self.hub = hub if hub is not None else uidrv.ControlHub()
        deps = UIDeps(
            win=self.win,
            input=self.input,
            frontend=self.frontend,
            audit_root=audit_root or Path(self._tmp.name),
            processor_ask=processor_ask,
            hil=hil,
            escalate=_escalate,
            signal=self.hub,
            clock=self.clock,
            sleep=self._sleep,
        )
        if cls is ScriptedDecider:
            self.dec = cls("mod", self.spec, hooks or {}, deps, answers=answers)
        else:
            self.dec = cls("mod", self.spec, hooks or {}, deps)

    async def _sleep(self, seconds: float) -> None:
        self.clock.advance(seconds)
        await asyncio.sleep(0)

    def cleanup(self) -> None:
        self._tmp.cleanup()

    def audit_dir(self) -> Path:
        return Path(self._tmp.name) / "ui-ops" / "mod"

    def jsonl_rows(self) -> list[dict]:
        rows: list[dict] = []
        for path in self.audit_dir().glob("*.jsonl"):
            for line in path.read_text(encoding="utf-8").splitlines():
                if line.strip():
                    rows.append(json.loads(line))
        return rows

    def steps(self) -> list[dict]:
        return [r for r in self.jsonl_rows() if r.get("type") == "step"]


async def _pin_capture(h: "Harness") -> None:
    """把抓帧钉死成假帧：单测绝不碰真桌面（base capture 会真的截屏）。"""

    async def fake_capture(rect=TARGET_RECT):
        return make_frame(7, rect=rect)

    h.dec.capture = fake_capture


# ────────────────────────── 会话打开 / HIL ──────────────────────────
@pytest.mark.asyncio
async def test_open_live_returns_overlap_and_registers_session():
    h = Harness()
    res = await h.dec.open()
    assert res.ok and res.mode == "live"
    assert res.overlap is True
    assert h.frontend.sessions == [True]
    assert h.hub.session_status()["sessions"][0]["module"] == "mod"
    h.cleanup()


@pytest.mark.asyncio
async def test_open_fails_when_renderer_silent():
    h = Harness(frontend=FakeFrontend(session_ok=False))
    res = await h.dec.open()
    assert not res.ok and "回执超时" in res.reason
    h.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("choice", ["reject", "timeout"])
async def test_hil_reject_and_timeout_block_all_injection(choice):
    async def _hil(payload, timeout):
        return choice

    h = Harness(hil=_hil)
    res = await h.dec.open()
    assert not res.ok
    assert h.input.taps == [] and h.input.clicks == []
    assert len(h.escalations) == 1          # 拒绝/超时都要通知主 Agent
    h.cleanup()


@pytest.mark.asyncio
async def test_hil_dry_run_decides_but_never_injects():
    async def _hil(payload, timeout):
        return "dry_run"

    h = Harness(hil=_hil, answers=["battle", "结束回合"])
    res = await h.dec.open()
    assert res.ok and res.mode == "dry_run"
    assert res.dry_run_until and res.dry_run_until > h.clock()
    d = await h.dec.step("出牌")
    assert d.ok and d.action == "space"
    assert h.input.taps == []
    row = h.steps()[-1]
    assert row["dry_run"] is True and row["injected"] is False
    assert row["inject_result"] == "dry_run"
    h.cleanup()


@pytest.mark.asyncio
async def test_hil_payload_has_three_buttons_and_full_summary():
    captured = {}

    async def _hil(payload, timeout):
        captured.update(payload)
        return "reject"

    h = Harness(hil=_hil)
    await h.dec.open()
    assert [b["value"] for b in captured["buttons"]] == ["dry_run", "allow", "reject"]
    assert captured["buttons"][0]["default"] is True
    for needle in ("Fake Game", "fake.exe", "777", "自动打牌", "打出第1张牌", "上限",
                   "服务条款", "拒绝", "会每步闪一次"):
        assert needle in captured["summary"], needle
    h.cleanup()


@pytest.mark.asyncio
async def test_open_fails_without_frontend_bounds():
    h = Harness(frontend=FakeFrontend())
    h.dec.deps.frontend.bounds = None
    res = await h.dec.open()
    assert not res.ok and "窗口边界" in res.reason
    h.cleanup()


@pytest.mark.asyncio
async def test_open_rejects_inconsistent_screen_coords():
    h = Harness(frontend=FakeFrontend(bounds=(0, 0, 4000, 3000)))
    res = await h.dec.open()
    assert not res.ok and "DPI" in res.reason
    h.cleanup()


@pytest.mark.asyncio
async def test_non_overlapping_frontend_never_blinks():
    h = Harness(answers=["battle", "结束回合"], frontend=FakeFrontend(bounds=(1200, 900, 1400, 1000)))
    res = await h.dec.open()
    assert res.ok and res.overlap is False
    await h.dec.step("出牌")
    assert h.frontend.blinks == []          # 不重叠 ⇒ 全程不眨眼
    h.cleanup()


# ────────────────────────── 决策 / 门限 ──────────────────────────
@pytest.mark.asyncio
async def test_union_path_asks_state_then_action_and_injects():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    d = await h.dec.step("该出牌了")
    assert d.ok and d.state == "battle" and d.state_source == "model"
    assert d.action == "space" and d.action_kind == "key"
    assert h.input.taps == ["space"]
    asked = h.dec.asked
    assert len(asked) == 2
    assert asked[0][1] == ("battle", "menu")                      # 状态问：选项是状态
    assert asked[1][1][:2] == ("battle::1", "battle::space")      # 动作问：并集
    assert "battle::click_hand" in asked[1][1]
    h.cleanup()


@pytest.mark.asyncio
async def test_classify_hook_skips_state_question():
    async def classify(f, *, step_id):
        return "menu"

    h = Harness(answers=["确认"], hooks={"classify": classify})
    await h.dec.open()
    d = await h.dec.step("确认")
    assert d.ok and d.state == "menu" and d.state_source == "hook"
    assert d.action == "space"
    assert len(h.dec.asked) == 1
    assert h.dec.asked[0][1] == ("space", "esc")
    h.cleanup()


@pytest.mark.asyncio
async def test_classify_hook_returning_undeclared_state_is_an_error():
    async def classify(f, *, step_id):
        return "nope"

    h = Harness(answers=["确认"], hooks={"classify": classify})
    await h.dec.open()
    d = await h.dec.step("确认")
    assert not d.ok and d.uncertain_reason == "hook_error"
    assert "未声明的状态" in d.note
    h.cleanup()


@pytest.mark.asyncio
async def test_state_and_action_inconsistency_is_refused():
    h = Harness(answers=["battle", "menu::esc"])
    await h.dec.open()
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "inconsistent"
    assert h.input.taps == []
    h.cleanup()


@pytest.mark.asyncio
async def test_unknown_option_key_is_refused():
    h = Harness(answers=["battle", "不存在的动作"])
    await h.dec.open()
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "unknown_option"
    assert h.input.taps == []
    h.cleanup()


@pytest.mark.asyncio
@pytest.mark.parametrize("answer,reason", [
    ({"choice": "battle", "confidence": 0.2, "abstain": 0.1}, "low_confidence"),
    ({"choice": "battle", "confidence": 0.9, "abstain": 0.5}, "abstain"),
    ({"choice": "battle", "confidence": 0.9, "abstain": 0.05,
      "probabilities": {"battle": 0.5, "menu": 0.45}}, "low_margin"),
    ({"choice": "battle", "confidence": 0.9, "abstain": 0.05, "noul": 0.5}, "noul_deadzone"),
    ({"choice": "battle", "confidence": 0.9, "abstain": 0.05, "valid": False},
     "invalid_distribution"),
])
async def test_gates_block_uncertain_decisions(answer, reason):
    h = Harness(answers=[answer, {"choice": "space", "confidence": 0.9, "abstain": 0.05}])
    await h.dec.open()
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == reason, d.uncertain_reason
    assert h.input.taps == []
    h.cleanup()


@pytest.mark.asyncio
async def test_uncertain_escalation_reports_evidence_paths():
    h = Harness(spec=make_spec(unknown_policy="escalate"), answers=[None, None])
    await h.dec.open()
    d = await h.dec.step("出牌")
    # 模型没给出选择（key=None）⇒ 判不确定并升级（unknown_policy="escalate"）
    assert not d.ok and d.uncertain_reason == "no_choice"
    assert d.escalate_reason == "no_choice"
    data, recall = h.escalations[0]
    assert data["reason"] == "no_choice"
    assert data["ops"] == "faustbot://agile/mod/ops"
    assert "faustbot://agile/mod/ops" in recall
    assert (h.audit_dir() / "last_escalation.json").exists()
    h.cleanup()


@pytest.mark.asyncio
async def test_union_over_twelve_actions_is_rejected_at_parse():
    actions = {f"key{i}": f"动作{i}" for i in range(13)}
    with pytest.raises(SpecError) as exc:
        make_spec(states={"many": {"describe": "很多动作", "actions": actions}})
    assert "上限 12" in str(exc.value)


@pytest.mark.asyncio
async def test_context_hook_is_truncated_and_attached():
    async def ctx_hook(f, *, step_id):
        return "血" * (HOOK_CONTEXT_MAX_CHARS + 50)

    h = Harness(answers=["battle", "结束回合"], hooks={"context": ctx_hook})
    await h.dec.open()
    d = await h.dec.step("出牌")
    assert d.ok
    question = h.dec.asked[1][0]
    assert "附加信息" in question
    assert question.count("血") == HOOK_CONTEXT_MAX_CHARS
    assert any(r.get("type") == "hook_context_truncated" for r in h.jsonl_rows())
    h.cleanup()


@pytest.mark.asyncio
async def test_hook_errors_end_session_after_three_strikes():
    async def classify(f, *, step_id):
        raise RuntimeError("boom")

    h = Harness(answers=["确认"], hooks={"classify": classify})
    await h.dec.open()
    for _ in range(3):
        d = await h.dec.step("确认")
        assert d.uncertain_reason == "hook_error"
    assert h.dec.state is SessionState.CLOSED
    assert h.escalations[0][0]["reason"] == "hook_error"
    assert "boom" in h.escalations[0][1]
    h.cleanup()


@pytest.mark.asyncio
async def test_unknown_hook_name_is_rejected_by_wiring(tmp_path):
    from agile_base import AgileContext

    ctx = AgileContext(None, None, "mod", data_dir=tmp_path)
    with pytest.raises(ValueError, match="未知的 limited_ui 钩子"):
        await ctx.limited_ui({
            "purpose": "x", "target": {"exe": "a.exe"},
            "states": {"s": {"describe": "d", "actions": {"1": "a"}}},
        }, hooks={"decide_action": lambda *a: None})


# ────────────────────────── 注入前复核 ──────────────────────────
@pytest.mark.asyncio
async def test_stale_frame_blocks_injection():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()

    async def moving_capture():
        moving_capture.n += 1
        return make_frame(moving_capture.n, pattern="noise")

    moving_capture.n = 0
    h.dec.capture = moving_capture
    d = await h.dec.decide("出牌")
    assert d.ok
    await h.dec.inject(d)
    assert h.input.taps == []
    row = h.steps()[-1]
    assert row["injected"] is False and row["inject_result"].startswith("stale_frame")
    h.cleanup()


@pytest.mark.asyncio
async def test_stable_frame_allows_injection_and_records_frame_changed():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    d = await h.dec.step("出牌")
    assert h.input.taps == ["space"]
    row = h.steps()[-1]
    assert row["injected"] is True and row["inject_result"] == "ok"
    assert row["frame_changed"] is False        # 注入后画面没变 → 调参回路能看到
    h.cleanup()


@pytest.mark.asyncio
async def test_click_action_uses_box_center_and_checks_window_under_point():
    h = Harness(answers=["battle", "点手牌区中央"])
    await h.dec.open()
    d = await h.dec.decide("出牌")
    assert d.ok and d.action_kind == "click"
    assert d.point == (500, 575)                # box=[100,800,900,950] 的中心映射到客户区
    await h.dec.inject(d)
    assert h.input.clicks == [(500, 575)]
    h.cleanup()


@pytest.mark.asyncio
async def test_occluded_click_point_is_refused():
    h = Harness(answers=["battle", "点手牌区中央"])
    await h.dec.open()
    h.win.root_at[(500, 575)] = OTHER_HWND     # 那个点上是别的窗口（可能就是我们自己的覆盖层）
    d = await h.dec.decide("出牌")
    await h.dec.inject(d)
    assert h.input.clicks == []
    row = h.steps()[-1]
    assert row["injected"] is False and row["inject_result"].startswith("occluded")
    h.cleanup()


@pytest.mark.asyncio
async def test_focus_loss_pauses_without_capturing_and_hints():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    h.win.foreground = OTHER_HWND
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "focus_lost"
    assert h.dec.state is SessionState.PAUSED
    assert h.frontend.hints[-1][0] == "lost"
    assert "Fake Game" in h.frontend.hints[-1][2]
    assert h.steps()[-1]["frame_digest"] == ""  # 失焦时连帧也没抓
    h.win.foreground = HWND
    d2 = await h.dec.step("出牌")
    assert d2.ok and h.dec.state is SessionState.ACTIVE
    h.cleanup()


@pytest.mark.asyncio
async def test_focus_lost_three_times_escalates():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    h.win.foreground = OTHER_HWND
    for _ in range(3):
        await h.dec.step("出牌")
    assert any(e[0]["reason"] == "focus_timeout" for e in h.escalations)
    h.cleanup()


@pytest.mark.asyncio
async def test_blink_is_wrapped_around_capture_and_injection():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    await h.dec.step("出牌")
    assert h.frontend.blinks[:2] == [True, False]     # 抓帧前后各眨一次
    assert h.frontend.blinks[2:4] == [True, False]    # 注入前后各一次
    h.cleanup()


@pytest.mark.asyncio
async def test_blink_ack_timeout_blocks_step_and_never_injects():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    h.frontend.blink_ok = False
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "blink_timeout"
    assert h.input.taps == []
    h.cleanup()


@pytest.mark.asyncio
async def test_inject_api_error_propagates():
    class BoomInput(FakeInput):
        async def key_tap(self, key: str, hold_ms: int = 30) -> None:
            raise RuntimeError("SendInput 被 UIPI 拦截")

    h = Harness(answers=["battle", "结束回合"], inp=BoomInput())
    await h.dec.open()
    d = await h.dec.decide("出牌")
    with pytest.raises(RuntimeError, match="UIPI"):
        await h.dec.inject(d)
    assert h.steps()[-1]["inject_result"] == "error[RuntimeError]"
    h.cleanup()


@pytest.mark.asyncio
async def test_failsafe_corner_stops_and_does_not_auto_resume():
    h = Harness(answers=["battle", "结束回合"] * 3)
    await h.dec.open()
    h.input.key_down("w")
    h.input.cursor = (0, 0)                 # 鼠标甩到左上角
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "failsafe_corner"
    assert h.input.pressed() == []          # 必须先松手
    assert h.dec.state is SessionState.PAUSED
    h.input.cursor = (1280, 800)
    h.clock.advance(300)
    assert not (await h.dec.step("出牌")).ok    # 不自动恢复（同热键语义）
    await h.dec.resume()
    assert (await h.dec.step("出牌")).ok
    h.cleanup()


@pytest.mark.asyncio
async def test_window_reopened_is_rebound_and_session_continues():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    h.win.alive = False                     # 句柄失效（游戏被重开）

    def reopen(target):
        h.win.alive = True                  # 新窗口是活的，但句柄变了
        h.win.hwnd = 0xBEEF
        h.win.foreground = 0xBEEF
        return WindowInfo(hwnd=0xBEEF, title="Fake Game", exe="fake.exe", pid=778,
                          path="C:/fake.exe", client_rect=TARGET_RECT)

    h.win.resolve = reopen
    d = await h.dec.step("出牌")
    assert d.ok and h.dec.window.hwnd == 0xBEEF
    assert any(r.get("type") == "window_rebound" for r in h.jsonl_rows())
    h.cleanup()


@pytest.mark.asyncio
async def test_window_gone_ends_session_and_escalates():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    h.win.alive = False

    def boom(target):
        from uidrv.win import WindowNotFoundError
        raise WindowNotFoundError("没了")

    h.win.resolve = boom
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "window_gone"
    assert h.dec.state is SessionState.CLOSED
    assert any(e[0]["reason"] == "window_gone" for e in h.escalations)
    h.cleanup()


@pytest.mark.asyncio
async def test_blink_probe_frame_is_captured_while_hidden():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    frame = await h.dec.capture_hidden()
    assert frame.image is not None
    assert h.frontend.blinks == [True, False]      # 藏 → 抓 → 显
    h.cleanup()


# ────────────────────────── 急停 / 上限 ──────────────────────────
@pytest.mark.asyncio
async def test_hotkey_stop_releases_keys_and_pauses_without_auto_resume():
    h = Harness(answers=["battle", "结束回合"] * 3)
    await h.dec.open()
    h.input.key_down("w")                    # 模拟"按着还没放"
    assert (await h.dec.step("出牌")).ok
    h.input.key_down("w")
    h.hub.request_stop("mod")
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "manual_stop"
    assert h.dec.state is SessionState.PAUSED
    assert h.input.pressed() == []           # 急停必须松手
    h.clock.advance(60)
    d2 = await h.dec.step("出牌")
    assert not d2.ok and h.dec.state is SessionState.PAUSED
    h.cleanup()


@pytest.mark.asyncio
async def test_rate_limit_ends_session():
    h = Harness(answers=["battle", "结束回合"] * 6)
    await h.dec.open()
    for _ in range(4):
        assert (await h.dec.step("出牌")).ok
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "limit_rate"
    assert h.dec.state is SessionState.CLOSED
    assert h.dec.closed_reason == "limit_rate"
    assert h.steps()[-1]["inject_result"] == "limit_rate"
    assert len([r for r in h.steps() if r["step_id"] == 5]) == 1   # 每步只有一行
    h.cleanup()


@pytest.mark.asyncio
async def test_max_actions_limit_ends_session():
    h = Harness(spec=make_spec(limits={"keys_per_sec": 10, "max_actions": 2,
                                       "max_session_s": 600, "hook_timeout_s": 2.0}),
                answers=["battle", "结束回合"] * 4)
    await h.dec.open()
    for _ in range(2):
        assert (await h.dec.step("出牌")).ok
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "limit_actions"
    assert h.dec.closed_reason == "limit_actions"
    h.cleanup()


@pytest.mark.asyncio
async def test_session_duration_limit_ends_session():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    h.clock.advance(601)
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "limit_session"
    assert h.dec.closed_reason == "limit_session"
    h.cleanup()


@pytest.mark.asyncio
async def test_dry_run_ends_by_itself():
    async def _hil(payload, timeout):
        return "dry_run"

    h = Harness(hil=_hil, answers=["battle", "结束回合"])
    await h.dec.open()
    h.clock.advance(DRY_RUN_SECONDS + 1)
    d = await h.dec.step("出牌")
    assert not d.ok and d.uncertain_reason == "dry_run_timeout"
    assert h.dec.closed_reason == "dry_run_timeout"
    assert any(e[0]["reason"] == "dry_run_timeout" for e in h.escalations)
    h.cleanup()


@pytest.mark.asyncio
async def test_ceiling_is_enforced():
    with pytest.raises(SpecError) as exc:
        make_spec(limits={"keys_per_sec": 11, "max_actions": 10, "max_session_s": 10,
                          "hook_timeout_s": 2.0})
    assert "天花板" in str(exc.value)


@pytest.mark.asyncio
async def test_unknown_spec_fields_are_rejected():
    with pytest.raises(SpecError):
        make_spec(whatever=1)
    with pytest.raises(SpecError):
        make_spec(states={"s": {"describe": "d", "actions": {"1": "a"}, "extra": 1}})
    with pytest.raises(SpecError):
        make_spec(states={"s": {"describe": "d",
                                "actions": {"c": {"click": [10, 10, 5, 40], "meaning": "x"}}}})
    with pytest.raises(SpecError):
        make_spec(states={"s": {"describe": "d",
                                "actions": {"c": {"click": [0, 0, 100, 2000], "meaning": "x"}}}})
    with pytest.raises(SpecError):
        make_spec(unknown_policy="whatever")


# ────────────────────────── 帧校验 / 升级 ──────────────────────────
@pytest.mark.asyncio
async def test_black_frames_latch_then_escalate():
    h = Harness(answers=["battle", "结束回合"] * 4)
    await h.dec.open()
    black = Frame(image=np.zeros((120, 160, 3), dtype="uint8"), digest="dead", phash=0,
                  rect=TARGET_RECT, captured_at=0.0)   # 纯黑（DRM/独占全屏的典型表现）

    async def black_capture():
        return black

    h.dec.capture = black_capture
    for _ in range(3):
        d = await h.dec.step("出牌")
        assert d.uncertain_reason == "frame_invalid"
        assert h.input.taps == []
    assert any(e[0]["reason"] == "frame_invalid" for e in h.escalations)
    assert len([r for r in h.jsonl_rows() if r.get("type") == "frame_invalid"]) == 1  # latch
    h.cleanup()


# ────────────────────────── 审计 ──────────────────────────
@pytest.mark.asyncio
async def test_audit_rows_have_every_documented_field_and_parse():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    await h.dec.step("出牌")
    rows = h.steps()
    assert len(rows) == 1
    row = rows[0]
    for field in ("ts", "session_id", "step_id", "dry_run", "state", "state_source",
                  "state_conf", "action", "action_kind", "point", "confidence", "abstain",
                  "probabilities", "gate_pass", "uncertain_reason", "escalate_reason",
                  "injected", "inject_result", "frame_digest", "frame_phash", "frame_changed",
                  "hook_ms", "capture_ms", "ask_ms", "inject_ms"):
        assert field in row, field
    assert row["action"] == "space" and row["injected"] is True
    assert row["frame_digest"] and row["frame_phash"]
    assert row["question_id"] == "action"
    assert (h.audit_dir() / "summary.json").exists()
    h.cleanup()


@pytest.mark.asyncio
async def test_blocked_then_injected_rows_and_summary_stats():
    h = Harness(answers=[{"choice": "battle", "confidence": 0.1, "abstain": 0.1},
                         {"choice": "space", "confidence": 0.9, "abstain": 0.05},
                         "battle", "结束回合"])
    await h.dec.open()
    await h.dec.step("出牌")
    await h.dec.resume()                    # 拿不准 ⇒ 暂停，等人/主 Agent 放行
    await h.dec.step("出牌")
    rows = h.steps()
    assert [r["injected"] for r in rows] == [False, True]
    assert rows[0]["uncertain_reason"] == "low_confidence"
    summary = json.loads((h.audit_dir() / "summary.json").read_text(encoding="utf-8"))
    assert summary["steps"] == 2 and summary["injected"] == 1 and summary["uncertain"] == 1
    assert summary["state_source"] == {"hook": 0, "model": 2}
    assert summary["questions"]["action"]["asked"] == 2
    h.cleanup()


@pytest.mark.asyncio
async def test_ops_text_lists_recent_steps():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    await h.dec.step("出牌")
    text = h.dec.vfs_nodes()["/agile/mod/ops/recent"][0]()
    assert "battle" in text and "space" in text
    h.cleanup()


def test_frames_saved_on_uncertainty_and_cleanup_by_count(tmp_path):
    writer = AuditWriter("mod", tmp_path, snapshot_every=1, keep_days=0, keep_images=2)
    f = make_frame(3)
    for i in range(5):
        writer.save_frame(f, i, "uncertain")
    assert len(list(writer.frames_dir.glob("*.jpg"))) == 5
    assert writer.cleanup() == 3
    left = sorted(p.name for p in writer.frames_dir.glob("*.jpg"))
    assert left == ["000003-uncertain.jpg", "000004-uncertain.jpg"]
    assert writer.latest_frame_path().name == "000004-uncertain.jpg"


def test_audit_dedup_and_staleness_constant():
    assert 0 < STALE_PHASH_MAX_DISTANCE <= 16
    a, b = make_frame(1), make_frame(2)
    assert a.distance(a) == 0 and a.distance(b) > 0


@pytest.mark.asyncio
async def test_vfs_nodes_exposed():
    h = Harness(answers=["battle", "结束回合"])
    await h.dec.open()
    nodes = h.dec.vfs_nodes()
    assert "/agile/mod/ops/recent" in nodes
    assert "/agile/mod/ops/summary" in nodes
    assert "/agile/mod/ops/last_escalation.json" in nodes
    assert "/agile/mod/ops/frame.jpg" in nodes
    assert "(尚无升级记录)" in nodes["/agile/mod/ops/last_escalation.json"][0]()
    h.cleanup()


@pytest.mark.asyncio
async def test_patch_states_revalidates_and_hot_swaps_actions():
    h = Harness()
    await h.dec.open()
    h.dec.patch_states({"battle": {"actions": {"x": "新动作"}}})
    assert "x" in h.dec.spec.states["battle"]["actions"]
    assert h.dec.spec.states["menu"]["actions"]          # 其它状态保留
    with pytest.raises(SpecError):
        h.dec.patch_states({"nope": {"actions": {"x": "y"}}})
    with pytest.raises(SpecError):
        h.dec.patch_states({})                            # 不允许空 patch
    h.cleanup()


# ────────────────────────── OmniJev 打包 ──────────────────────────
@pytest.mark.asyncio
async def test_omnijev_packs_state_and_action_into_one_invoke():
    calls: list[dict] = []

    async def ask(payload, config):
        calls.append(payload)
        return {"answers": {
            "state": {"choice": "battle", "probabilities": {"battle": 0.9, "menu": 0.05},
                      "abstain": 0.05, "valid": True, "confidence": 0.85, "latency_s": 0.66},
            "action": {"choice": "battle::space", "probabilities": {"battle::space": 0.8},
                       "abstain": 0.1, "valid": True, "confidence": 0.7, "latency_s": 0.66},
        }, "latency_s": 1.32}

    h = Harness(cls=OmniJevDecider, processor_ask=ask)
    await h.dec.open()
    await _pin_capture(h)
    d = await h.dec.step("出牌")
    assert len(calls) == 1                       # 状态问 + 动作问必须在同一次 invoke
    questions = calls[0]["questions"]
    assert set(questions) == {"state", "action"}
    assert [o["key"] for o in questions["action"]["options"]][:2] == ["battle::1",
                                                                     "battle::space"]
    assert questions["action"]["options"][0]["text"] == "打出第1张牌"
    assert calls[0]["frames"][0].shape == (120, 160, 3)
    assert h.dec.deps.processor_ask is ask
    assert d.ok and d.action == "space" and d.latency_total_s == 1.32
    h.cleanup()


@pytest.mark.asyncio
async def test_omnijev_uses_classify_subspace_single_question():
    calls: list[dict] = []

    async def ask(payload, config):
        calls.append(payload)
        return {"answers": {"action": {"choice": "esc", "probabilities": {"esc": 0.9},
                                       "abstain": 0.05, "valid": True, "confidence": 0.9}}}

    async def classify(f, *, step_id):
        return "menu"

    h = Harness(cls=OmniJevDecider, processor_ask=ask, hooks={"classify": classify})
    await h.dec.open()
    await _pin_capture(h)
    d = await h.dec.step("返回")
    assert set(calls[0]["questions"]) == {"action"}
    assert [o["key"] for o in calls[0]["questions"]["action"]["options"]] == ["space", "esc"]
    assert d.ok and d.action == "esc" and d.state_source == "hook"
    h.cleanup()


@pytest.mark.asyncio
async def test_omnijev_failures_escalate_after_three():
    async def ask(payload, config):
        raise RuntimeError("Processor 未就绪")

    h = Harness(cls=OmniJevDecider, processor_ask=ask)
    await h.dec.open()
    await _pin_capture(h)
    for _ in range(3):
        d = await h.dec.step("出牌")
        assert d.uncertain_reason == "ask_failed"
    assert any(e[0]["reason"] == "processor_error" for e in h.escalations)
    assert {r["uncertain_reason"] for r in h.steps()} == {"ask_failed"}
    assert h.input.taps == []
    h.cleanup()
