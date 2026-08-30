from typing import List, Union, Literal, Optional
import datetime
import time
import queue
import json
from pathlib import Path
import threading
import os
import random

import faust_backend.config_loader as conf
import faust_backend.nimble as nimble
from pydantic import BaseModel, Field, field_validator
from faust_backend.logger import get_logger

# 惰性解析 triggers 文件路径 — 不在 import 时读取文件
_store_lock = threading.RLock()
log = get_logger("faust.trigger")


def _get_triggers_path() -> Path:
    return Path(conf.CONFIG_ROOT) / "agents" / Path(conf.AGENT_NAME) / "triggers.json"


exitflag = False
trigger_queue: "queue.Queue[dict]" = queue.Queue()
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


def _emit_trigger(trigger_payload: dict):
    log.debug("触发 trigger: %s", trigger_payload)
    payload = _apply_fire_filters(trigger_payload)
    if payload is None:
        return False
    trigger_queue.put(payload)
    from faust_backend.runtime import state
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


def trigger_watchdog_thread_main(poll_interval: float = 0.5):
    ensure_store = _ensure_store_loaded()  # 在线程中安全加载
    while True:
        if exitflag:
            return  # exit thread
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
                            if eval(trig.eval_code):
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

def get_next_trigger(timeout: Optional[float] = None):
    try:
        return trigger_queue.get(timeout=timeout)
    except queue.Empty:
        return None


def append_trigger(trigger_dict_or_str: dict | str):
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
    trigger = _apply_append_filters(trigger)
    from faust_backend.runtime import state
    pm = getattr(state, 'plugin_manager', None)
    if pm:
        # append_trigger 是同步入口（同步工具/to_thread 调用），使用同步桥接执行
        results = pm._call_pluggy_hook_sync('trigger_append', payload=trigger, ctx=None)
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


def update_trigger(trigger_id: str, trigger: dict | str) -> None:
    """Update (upsert) one trigger by id."""
    if isinstance(trigger, str):
        trigger = json.loads(trigger)
    if not isinstance(trigger, dict):
        raise ValueError("trigger must be dict or JSON string")

    payload = dict(trigger)
    payload["id"] = trigger_id
    append_trigger(payload)


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
def has_queue_task():
    return not trigger_queue.empty()
if __name__ == "__main__":
    append_trigger({
        "id": "CORE_HEARTBEAT",
        "type": "interval",
        "interval_seconds": 300,
        "recall_description": "核心心跳触发器，不要修改，每5分钟触发一次，用于Agent执行周期性任务或自我检查。"
    })
    log.info("初始 triggers: %s", get_trigger_information())
    exit(0)
    # test watchdog thread
    append_trigger({
        "id": "test_interval",
        "type": "interval",
        "interval_seconds": 5
    })
    append_trigger({
        "id": "test_datetime",
        "type": "datetime",
        "target": (datetime.datetime.now() + datetime.timedelta(seconds=10)).isoformat()
    })
    append_trigger({
        "id": "test_pyeval",
        "type": "py-eval",
        "eval_code": "random.random() < 0.1" 
    })
    start_trigger_watchdog_thread()
    log.info("Trigger 看门狗线程已启动")
    while True:
        trig = get_next_trigger(timeout=1.0)
        if trig:
            log.info("Trigger 触发: %s", trig)
        else:
            log.debug("上一秒无 trigger 触发")