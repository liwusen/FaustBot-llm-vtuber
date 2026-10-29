from typing import List, Union, Literal, Optional
import builtins
import datetime
import time
import queue
import json
import csv
from pathlib import Path
import threading
import os

import faust_backend.core.config as conf
import faust_backend.nimble as nimble
from faust_backend.agent.runtime.turn import presence as frontend_presence
from pydantic import BaseModel, Field, field_validator
from faust_backend.core.log import get_logger

# 惰性解析 triggers 文件路径 — 不在 import 时读取文件
_store_lock = threading.RLock()
log = get_logger("faust.trigger")


class TriggerRefused(RuntimeError):
    """触发被拒绝（D19：前端离线时禁止一切触发）。

    只在**产生侧**抛出：只有产生侧能对调用者说话。``reason`` 供 HTTP 层映射成
    409 响应体 / 插件调用者判断原因。
    """

    def __init__(self, reason: str = "no_frontend", *, source: str = "") -> None:
        self.reason = str(reason or "no_frontend")
        self.source = str(source or "")
        detail = f"source={self.source}" if self.source else "source=unknown"
        super().__init__(f"触发被拒绝（{self.reason}，{detail}）")


def _get_triggers_path() -> Path:
    return Path(conf.CONFIG_ROOT) / "agents" / Path(conf.AGENT_NAME) / "triggers.json"


exitflag = False
trigger_queue: "queue.Queue[dict]" = queue.Queue()
batched_queue: "queue.Queue[dict]" = queue.Queue()
last_interaction_ts: float = 0.0  # 最近一次用户交互时刻（chat.py 更新），0=从未交互
_append_filters = []
_fire_filters = []


def set_append_filters(filters):
    global _append_filters
    _append_filters = list(filters or [])


def set_fire_filters(filters):
    global _fire_filters
    _fire_filters = list(filters or [])


def _apply_append_filters(trigger_payload: dict):
    payload = dict(trigger_payload or {})
    for f in _append_filters:
        try:
            payload = f(payload)
            if payload is None:
                return None
            if not isinstance(payload, dict):
                raise ValueError("append filter must return dict or None")
        except Exception as e:
            log.warning("append filter error: %s", e)
            return None
    return payload


def _apply_fire_filters(trigger_payload: dict):
    payload = dict(trigger_payload or {})
    for f in _fire_filters:
        try:
            payload = f(payload)
            if payload is None:
                return None
            if not isinstance(payload, dict):
                raise ValueError("fire filter must return dict or None")
        except Exception as e:
            log.warning("fire filter error: %s", e)
            return None
    return payload


def _events_dir() -> Path:
    return Path(conf.CONFIG_ROOT) / "agents" / Path(conf.AGENT_NAME) / "events"


def _source_from_id(trigger_id: str) -> str:
    tid = str(trigger_id or "")
    return tid.split("::", 1)[0] if "::" in tid else ""


def _refusal_source(payload: dict) -> str:
    """拒绝计数/日志用的来源标识：优先触发器 id 的来源前缀，其次事件名/类型。"""
    src = _source_from_id(payload.get("id"))
    if src:
        return src
    return str(payload.get("event_name") or payload.get("type") or "unknown")


def _refuse_trigger(payload: dict, *, reason: str = "no_frontend") -> None:
    """记录一次被拒绝的触发：日志 + 计数（不写 fire 留痕、不入队）。"""
    source = _refusal_source(payload)
    frontend_presence.note_refused(source=source, reason=reason)
    log.warning(
        "前端离线，拒绝触发（%s）：id=%s source=%s",
        reason,
        payload.get("id"),
        source,
    )


def _log_event_csv(payload: dict):
    """事件留痕：任何触发器 fire 时追加一行 CSV。失败只记日志，不阻断主链路。"""
    try:
        d = _events_dir()
        d.mkdir(parents=True, exist_ok=True)
        now = datetime.datetime.now()
        path = d / f"{now:%Y%m%d}.csv"
        row = [
            now.isoformat(timespec="seconds"),
            _source_from_id(payload.get("id")),
            str(payload.get("type") or ""),
            str(payload.get("priority") or "normal"),
            str(payload.get("id") or ""),
            str(payload.get("recall_description") or ""),
            json.dumps(payload, ensure_ascii=False, default=str),
        ]
        with path.open("a", newline="", encoding="utf-8") as f:
            csv.writer(f).writerow(row)
    except Exception as e:
        log.warning("事件留痕写入失败: %s", e)


def _emit_trigger(trigger_payload: dict):
    # D19 离线禁触：所有 fire 的必经之路，在 fire filters **之前**判定。
    # 不入队、不写 fire 留痕、不动 store（到点即弃，回连时不存在积压补发）。
    if not frontend_presence.is_online():
        _refuse_trigger(trigger_payload or {})
        return False
    log.debug("触发 trigger: %s", trigger_payload)
    payload = _apply_fire_filters(trigger_payload)
    if payload is None:
        return False
    if payload.get("priority") == "batched":
        batched_queue.put(payload)
    else:
        trigger_queue.put(payload)
    _log_event_csv(payload)
    from faust_backend.agent.runtime import state
    pm = state.plugin_manager
    if pm:
        # watchdog 线程/同步回调上下文，使用同步桥接执行（可能含异步 hook 实现）
        results = pm._call_pluggy_hook_sync('trigger_fire', payload=payload, ctx=None)
        if results:
            for item in results:
                if item is None:
                    return False
                if isinstance(item, dict):
                    payload = item
    return True

class BaseTrigger(BaseModel):
    id: str
    remove_when: Optional[str] = None
    priority: Literal["interrupt", "normal", "batched"] = "normal"
    type: str
    recall_description: Optional[str] = None
    lifespan: Optional[int] = None
    run_background: bool = False
    created_at: float = Field(default_factory=time.time)

    model_config = {"extra": "forbid"}


class DateTimeTrigger(BaseTrigger):
    type: Literal["datetime"]
    target: datetime.datetime

    @field_validator("target", mode="before")
    def parse_target(cls, v):
        if isinstance(v, str):
            # accept 'YYYY-MM-DD HH:MM:SS' or ISO format
            try:
                return datetime.datetime.strptime(v, "%Y-%m-%d %H:%M:%S")
            except Exception:
                return datetime.datetime.fromisoformat(v)
        return v


class IntervalTrigger(BaseTrigger):
    type: Literal["interval"]
    interval_seconds: int = Field(..., ge=1)
    last_triggered: float = Field(default_factory=time.time)


class PyEvalTrigger(BaseTrigger):
    type: Literal["py-eval"]
    eval_code: str


class EventTrigger(BaseTrigger):
    type: Literal["event"]
    event_name: str
    callback_id: Optional[str] = None
    payload: Optional[dict] = None


class NimbleExpireTrigger(BaseTrigger):
    type: Literal["nimble-expire"]
    callback_id: str
    target: datetime.datetime


Trigger = Union[DateTimeTrigger, IntervalTrigger, PyEvalTrigger, EventTrigger, NimbleExpireTrigger]


class TriggerStore(BaseModel):
    watchdog: List[Trigger] = Field(default_factory=list)

    def save(self):
        path = _get_triggers_path()
        data = {"watchdog": [t.model_dump() for t in self.watchdog]}
        path.parent.mkdir(parents=True, exist_ok=True)
        with path.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4, default=str)

    @classmethod
    def load(cls) -> "TriggerStore":
        path = _get_triggers_path()
        if not path.exists():
            store = cls()
            store.save()
            return store
        try:
            raw = json.load(path.open("r", encoding="utf-8"))
            items = []
            for t in raw.get("watchdog", []):
                ttype = t.get("type")
                if ttype == "datetime":
                    items.append(DateTimeTrigger.model_validate(t))
                elif ttype == "interval":
                    items.append(IntervalTrigger.model_validate(t))
                elif ttype == "py-eval":
                    items.append(PyEvalTrigger.model_validate(t))
                elif ttype == "event":
                    items.append(EventTrigger.model_validate(t))
                elif ttype == "nimble-expire":
                    items.append(NimbleExpireTrigger.model_validate(t))
                else:
                    # skip unsupported
                    continue
            store = cls(watchdog=items)
            return store
        except Exception as e:
            log.error("加载 triggers 文件失败: %s", e)
            # create fresh store and overwrite corrupted file
            store = cls()
            store.save()
            return store


def _ensure_store_loaded() -> TriggerStore:
    """首次加载 _store，或重新加载（在 agent 切换后）。"""
    global _store
    with _store_lock:
        if _store is None:
            _store = TriggerStore.load()
    return _store


# module-level store — 惰性加载，避免 import 时依赖 conf.AGENT_NAME
_store: TriggerStore | None = None


def _poll_watchdog_once(ensure_store: TriggerStore) -> None:
    """轮询一轮定时/事件触发器（watchdog 线程与单测共用）。

    前端离线时整轮跳过（D19）：不 fire、不改 store。定时触发器因此保持"到期"
    状态，回连后最多补一次——**不是**把离线期间错过的每一次都补发（离线期间
    没有任何唤醒进入 inbox，不存在积压）。
    """
    if not frontend_presence.is_online():
        return
    now = datetime.datetime.now()
    with _store_lock:
        for trig in list(ensure_store.watchdog):
            try:
                if trig.lifespan is not None and trig.created_at + trig.lifespan <= time.time():
                    try:
                        ensure_store.watchdog.remove(trig)
                        ensure_store.save()
                    except Exception:
                        pass
                    continue
                if trig.type == "datetime":
                    if now >= trig.target:
                        _emit_trigger(trig.model_dump())
                        try:
                            ensure_store.watchdog.remove(trig)
                            ensure_store.save()
                        except Exception:
                            pass
                elif trig.type == "interval":
                    if time.time() - trig.last_triggered >= trig.interval_seconds:
                        _emit_trigger(trig.model_dump())
                        trig.last_triggered = time.time()
                        ensure_store.save()
                elif trig.type == "py-eval":
                    try:
                        if eval(trig.eval_code, {"__builtins__": builtins}, _pyeval_context()):
                            _emit_trigger(trig.model_dump())
                    except Exception as e:
                        log.error("评估 trigger %s 时出错: %s", trig.id, e)
                elif trig.type == "event":
                    log.info("Event trigger fired: %s with payload: %s", trig.event_name, trig.payload)
                    _emit_trigger(trig.model_dump())
                    ensure_store.watchdog.remove(trig)
                    ensure_store.save()
                elif trig.type == "nimble-expire":
                    if now >= trig.target:
                        _emit_trigger(trig.model_dump())
                        try:
                            ensure_store.watchdog.remove(trig)
                            ensure_store.save()
                        except Exception:
                            pass
                else:
                    _emit_trigger(trig.model_dump())
            except Exception as e:
                log.error("Watchdog 循环错误 %s: %s", getattr(trig, 'id', None), e)


def trigger_watchdog_thread_main(poll_interval: float = 0.5):
    ensure_store = _ensure_store_loaded()  # 在线程中安全加载
    while True:
        if exitflag:
            return  # exit thread
        _poll_watchdog_once(ensure_store)
        time.sleep(poll_interval)
_thread=None
def start_trigger_watchdog_thread():
    global _thread
    _thread = threading.Thread(target=trigger_watchdog_thread_main, daemon=True)
    _thread.start()
def stop_trigger_watchdog_thread():
    global exitflag
    exitflag=True
    if _thread is not None:
        _thread.join()

def pop_next_trigger():
    """非阻塞取一条触发器（紧急与普通共用一个 inbox，优先级由调度器裁定）。

    取用规则（播放闸门 / interrupt 越过排队中的 normal / 优先级）全部在
    ``runtime/scheduler.py``：这里只做"从 inbox 拿一条"，拿不到就返回 None。
    """
    try:
        return trigger_queue.get_nowait()
    except queue.Empty:
        return None


BATCH_WINDOW_SEC: float = 30.0  # batched 级事件合并窗口（秒）；修改此值调整打扰频率


def drain_batched() -> list[dict]:
    """取走 batched 队列全部积压（消费侧聚合用，非阻塞）。"""
    out = []
    while True:
        try:
            out.append(batched_queue.get_nowait())
        except queue.Empty:
            return out


def has_batched() -> bool:
    return not batched_queue.empty()


def format_batch_injection(items: list[dict], first_ts: float) -> str:
    """把 batched 队列积压打包为一条注入文本（消费侧调用）。"""
    summaries = "\n".join(
        f"· {it.get('recall_description') or it.get('event_name') or it.get('id') or str(it)[:80]}"
        for it in items
    )
    first_hms = datetime.datetime.fromtimestamp(first_ts).strftime("%H:%M:%S")
    return (
        f"<Trigger>触发器唤醒了你。以下 {len(items)} 条 batched 级事件已合并"
        f"（自 {first_hms} 起），请统一处理：\n{summaries}"
    )


async def render_trigger_text(task: dict) -> str | None:
    """把触发器 payload 渲染成注入 Agent 的文本；返回 None = 判定为不注入。

    渲染（``<Trigger>`` 模板）与黑名单判断属于**触发器域**：这些规则只对触发器
    成立，放在消费循环里会让"怎么描述一条触发"散落在路由层。消费侧（命令 WS）
    只负责把 payload 变成一条请求交给调度器。
    """
    from faust_backend import live_mode

    trigger_text = f"<Trigger>触发器唤醒了你，请根据触发器内容执行相应操作。{str(task)}"
    if not isinstance(task, dict):
        return trigger_text
    ttype = task.get("type")
    callback_id = task.get("callback_id")
    if ttype == "event" and task.get("event_name") == "nimble_message" and callback_id:
        msg_payload = (task.get("payload") or {}).get("payload")
        if isinstance(msg_payload, dict) and msg_payload.get("type") == "window-closed":
            # 关闭时 console 节点已随会话销毁，不能再让 Agent 去读
            trigger_text = (
                f"<Trigger>灵动交互窗口 {callback_id} 已被用户关闭"
                f"（reason={msg_payload.get('reason')}）。窗口与 console 节点已销毁，"
                f"不要再读写 faustbot://nimble/{callback_id}/console；如需继续互动请重新创建窗口。"
            )
        else:
            trigger_text = (
                f"<Trigger>灵动交互窗口消息。callback_id={callback_id}，"
                f"payload={json.dumps(msg_payload, ensure_ascii=False)}。"
                f"完整对话记录在 faustbot://nimble/{callback_id}/console，"
                f"如需回复请用 write 工具向该 console 路径写入消息。"
            )
    elif ttype == "event" and task.get("event_name") == "blive_danmaku":
        payload = task.get("payload") or {}
        uname = payload.get("uname", "匿名")
        msg = payload.get("msg", "")
        if live_mode.is_tts_blacklisted(msg):
            return None
        trigger_text = f"<Trigger>直播间弹幕: {uname}: {msg}"
    elif ttype == "event" and task.get("event_name") == "mc_event":
        payload = task.get("payload") or {}
        trigger_text = (
            "<Trigger>Minecraft事件唤醒了你。"
            f"事件类型={payload.get('mc_event_type')}，"
            f"事件详情={json.dumps(payload, ensure_ascii=False)}。"
            "请结合当前游戏状态，决定是否调用 Minecraft 工具继续操作。"
        )
    elif ttype == "nimble-expire" and callback_id:
        await nimble.finalize_close(callback_id, reason="expired")
        trigger_text = f"<Trigger>灵动交互窗口已过期关闭。callback_id={callback_id}。如有必要，请重新创建更明确的新窗口。"
    return trigger_text


def note_user_interaction():
    global last_interaction_ts
    last_interaction_ts = time.time()


def _pyeval_context() -> dict:
    """py-eval 触发器可用的只读常量快照（每次轮询重算）。"""
    now = datetime.datetime.now()
    now_epoch = time.time()
    idle_sec = (now_epoch - last_interaction_ts) if last_interaction_ts > 0 else 1e9
    return {
        "HOUR": now.hour,
        "MINUTE": now.minute,
        "SECOND": now.second,
        "WEEKDAY": now.weekday(),  # 0=周一 … 6=周日
        "MONTH": now.month,
        "DAY": now.day,
        "EPOCH": now_epoch,
        "FREETIME_MIN": idle_sec / 60.0,
        "USER_IDLE_SEC": idle_sec,
    }


async def append_trigger(trigger_dict_or_str: dict | str):
    """Append a new trigger to the store.

    Supported trigger types are 'datetime', 'interval', 'py-eval', 'event', and 'nimble-expire'.

    TRIGGER EXAMPLES:
    {
        "id": "datetime_trigger",
        "type": "datetime",
        "target": "2023-01-01T00:00:00Z"
    }
    
    {
        "id": "interval_trigger",
        "type": "interval",
        "interval_seconds": 3600
    }
    
    {
        "id": "py_eval_trigger",
        "type": "py-eval",
        "eval_code": "some_python_expression"
    }


    Args:
    Args:
        trigger_dict_or_str (dict | str): The trigger to append.
    Raises:
        ValueError: If the trigger type is unsupported or invalid.
    """    
    if isinstance(trigger_dict_or_str, str):
        try:
            trigger = json.loads(trigger_dict_or_str)
        except Exception as e:
            log.error("无效的 trigger JSON 字符串: %s", e)
            raise
    else:
        trigger = trigger_dict_or_str
    # D19：`event` 型触发器是"立刻 fire 一次"的请求（watchdog 每个轮询周期就把它
    # fire 掉），离线时必须让调用者立刻看到拒绝，而不是"写入成功、永远不 fire"。
    # 定时类（interval/datetime/py-eval/nimble-expire）是"排期"而非"触发"，
    # 允许先入 store，由 watchdog 在到期时按同一条规则拒绝。
    if isinstance(trigger, dict) and str(trigger.get("type") or "") == "event":
        if not frontend_presence.is_online():
            refused = TriggerRefused(
                "no_frontend", source=str(trigger.get("id") or trigger.get("event_name") or "")
            )
            _refuse_trigger(trigger, reason=refused.reason)
            raise refused
    trigger = _apply_append_filters(trigger)
    from faust_backend.agent.runtime import state
    pm = getattr(state, 'plugin_manager', None)
    if pm:
        # 这里必须走 async hook：append_trigger 的调用点（Agent 工具、HTTP 路由、
        # 插件 API）大多就在事件循环上，同步桥在环上只能退化成工作线程 + 超时。
        results = await pm._call_pluggy_hook('trigger_append', payload=trigger, ctx=None)
        if results:
            for item in results:
                if item is None:
                    trigger = None
                    break
                if isinstance(item, dict):
                    trigger = item
    if trigger is None:
        raise ValueError("Trigger blocked by append filters")
    global _store
    try:
        ttype = trigger.get("type")
        if ttype == "datetime":
            t = DateTimeTrigger.model_validate(trigger)
        elif ttype == "interval":
            t = IntervalTrigger.model_validate(trigger)
        elif ttype == "py-eval":
            t = PyEvalTrigger.model_validate(trigger)
        elif ttype == "event":
            t = EventTrigger.model_validate(trigger)
        elif ttype == "nimble-expire":
            t = NimbleExpireTrigger.model_validate(trigger)
        else:
            raise ValueError(f"Unsupported trigger type: {ttype}")
    except Exception as e:
        log.error("无效的 trigger payload: %s", e)
        raise
    
    # remove any existing with same id, then append & save
    with _store_lock:
        store = _ensure_store_loaded()
        store.watchdog = [x for x in store.watchdog if x.id != t.id]
        store.watchdog.append(t)
        store.save()


def delete_trigger(trigger_id: str):
    with _store_lock:
        store = _ensure_store_loaded()
        before = len(store.watchdog)
        store.watchdog = [t for t in store.watchdog if t.id != trigger_id]
        if len(store.watchdog) != before:
            try:
                store.save()
            except Exception as e:
                log.error("保存后触发失败: %s", e)
    drop_pending_wakeups(trigger_id)


def drop_pending_wakeups(trigger_id: str) -> int:
    """删除触发器时两处都查：inbox（两个队列）+ 调度器队列（D10）。

    ``trigger_id`` 作为 merge_key 贯穿"还没被消费的唤醒"；不查这一处，删除后
    那些已经进入 inbox/调度器队列的唤醒仍会打印一次。
    """
    removed = 0
    for q in (trigger_queue, batched_queue):
        with q.mutex:
            kept = [
                item
                for item in q.queue
                if not (isinstance(item, dict) and item.get("id") == trigger_id)
            ]
            removed += len(q.queue) - len(kept)
            if len(kept) != len(q.queue):
                q.queue.clear()
                q.queue.extend(kept)
    try:
        from faust_backend.agent.runtime import scheduler

        removed += scheduler.get_scheduler().cancel_merge_key(trigger_id, reason="trigger_deleted")
    except Exception as e:  # noqa: BLE001 - 删除触发器不得因调度器异常而失败
        log.warning("清理调度器队列中的触发器失败: %s", e)
    if removed:
        log.info("删除触发器 %s：同时撤掉 %d 条未执行的唤醒", trigger_id, removed)
    return removed


def list_triggers() -> list[dict]:
    """Return all persisted triggers as plain dicts."""
    with _store_lock:
        store = _ensure_store_loaded()
        try:
            return [t.model_dump() for t in store.watchdog]
        except Exception as e:
            log.error("列出 triggers 失败: %s", e)
            return []


def get_trigger(trigger_id: str) -> dict | None:
    """Get one trigger by id."""
    with _store_lock:
        store = _ensure_store_loaded()
        try:
            for t in store.watchdog:
                if t.id == trigger_id:
                    return t.model_dump()
        except Exception as e:
            log.error("获取 trigger %s 失败: %s", trigger_id, e)
    return None


async def update_trigger(trigger_id: str, trigger: dict | str) -> None:
    """Update (upsert) one trigger by id."""
    if isinstance(trigger, str):
        trigger = json.loads(trigger)
    if not isinstance(trigger, dict):
        raise ValueError("trigger must be dict or JSON string")

    payload = dict(trigger)
    payload["id"] = trigger_id
    await append_trigger(payload)


def get_trigger_information() -> str:
    # return formatted JSON of current store
    with _store_lock:
        store = _ensure_store_loaded()
        try:
            data = {"watchdog": [t.model_dump() for t in store.watchdog]}
            return json.dumps(data, indent=4, ensure_ascii=False, default=str)
        except Exception as e:
            log.error("序列化 triggers 失败: %s", e)
    return "{}"


def clear_triggers():
    with _store_lock:
        store = _ensure_store_loaded()
        store.watchdog.clear()
        try:
            store.save()
        except Exception as e:
            log.error("清理后保存失败: %s", e)
def drain_inbox_as_refused(reason: str = "frontend_disconnected") -> int:
    """连接断开时把 inbox 里尚未消费的唤醒逐条记为 refused 并清空（D19-c）。

    否则回连时会冒出"断开前残留的几条"——它们既不属于离线期间的合法触发，
    也不该在无人消费时静默留着。返回被清空的条数。
    """
    dropped = 0
    for q in (trigger_queue, batched_queue):
        while True:
            try:
                item = q.get_nowait()
            except queue.Empty:
                break
            if isinstance(item, dict):
                frontend_presence.note_refused(
                    source=_refusal_source(item), reason=reason
                )
            dropped += 1
    if dropped:
        log.info("前端断开：清空 %d 条未消费的唤醒并记为 refused（%s）", dropped, reason)
    return dropped


def get_trigger_queue_snapshot() -> list[dict]:
    """返回触发器队列当前内容的快照（不消费队列，供调试/监控使用）。合并 urgent 与 batched 两个队列。"""
    with trigger_queue.mutex:
        items = list(trigger_queue.queue)
    with batched_queue.mutex:
        items += list(batched_queue.queue)
    return items


if __name__ == "__main__":
    import asyncio

    async def _seed_core_heartbeat() -> None:
        await append_trigger({
            "id": "CORE_HEARTBEAT",
            "type": "interval",
            "interval_seconds": 300,
            "recall_description": "核心心跳触发器，不要修改，每5分钟触发一次，用于Agent执行周期性任务或自我检查。"
        })
        log.info("初始 triggers: %s", get_trigger_information())

    asyncio.run(_seed_core_heartbeat())