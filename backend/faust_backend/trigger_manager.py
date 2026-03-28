from typing import List, Union, Literal, Optional
import datetime
import time
import queue
import json
from pathlib import Path
import threading
import os
import random

try:
    import faust_backend.config_loader as conf
    import faust_backend.nimble as nimble
except ImportError:
    import config_loader as conf
    import nimble
from pydantic import BaseModel, Field, field_validator

TRIGGERS_FILE = Path("agents") / Path(conf.AGENT_NAME) / "triggers.json"
print(f"[trigger_manager] Using triggers file: {TRIGGERS_FILE}")
print(f"[trigger_manager] Trigger file content: {TRIGGERS_FILE.read_text(encoding='utf-8') if TRIGGERS_FILE.exists() else 'File does not exist'}")
exitflag=False
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
            print(f"[trigger_manager] append filter error: {e}")
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
            print(f"[trigger_manager] fire filter error: {e}")
            return None
    return payload


def _emit_trigger(trigger_payload: dict):
    payload = _apply_fire_filters(trigger_payload)
    if payload is None:
        return False
    trigger_queue.put(payload)
    return True

class BaseTrigger(BaseModel):
    id: str
    type: str
    recall_description: Optional[str] = None
    lifespan: Optional[int] = None
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


class NimbleRemindTrigger(BaseTrigger):
    type: Literal["nimble-reminder"]
    callback_id: str
    interval_seconds: int = Field(..., ge=1)
    last_triggered: float = Field(default_factory=time.time)


class NimbleExpireTrigger(BaseTrigger):
    type: Literal["nimble-expire"]
    callback_id: str
    target: datetime.datetime


Trigger = Union[DateTimeTrigger, IntervalTrigger, PyEvalTrigger, EventTrigger, NimbleRemindTrigger, NimbleExpireTrigger]


class TriggerStore(BaseModel):
    watchdog: List[Trigger] = Field(default_factory=list)

    def save(self):
        # use model_dump 并确保 datetime 可序列化
        data = {"watchdog": [t.model_dump() for t in self.watchdog]}
        TRIGGERS_FILE.parent.mkdir(parents=True, exist_ok=True)
        with TRIGGERS_FILE.open("w", encoding="utf-8") as f:
            json.dump(data, f, ensure_ascii=False, indent=4, default=str)

    @classmethod
    def load(cls) -> "TriggerStore":
        if not TRIGGERS_FILE.exists():
            store = cls()
            store.save()
            return store
        try:
            raw = json.load(TRIGGERS_FILE.open("r", encoding="utf-8"))
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
                elif ttype == "nimble-reminder":
                    items.append(NimbleRemindTrigger.model_validate(t))
                elif ttype == "nimble-expire":
                    items.append(NimbleExpireTrigger.model_validate(t))
                else:
                    # skip unsupported
                    continue
            store = cls(watchdog=items)
            return store
        except Exception as e:
            print(f"[trigger_manager] Error loading triggers file: {e}")
            # create fresh store and overwrite corrupted file
            store = cls()
            store.save()
            return store


# module-level store
_store = TriggerStore.load()


def trigger_watchdog_thread_main(poll_interval: float = 0.5):
    while True:
        if exitflag:
            return # exit thread
        now = datetime.datetime.now()
        for trig in list(_store.watchdog):
            try:
                if trig.lifespan is not None and trig.created_at + trig.lifespan <= time.time():
                    try:
                        _store.watchdog.remove(trig)
                        _store.save()
                    except Exception:
                        pass
                    continue
                if trig.type == "datetime":
                    if now >= trig.target:
                        _emit_trigger(trig.model_dump())
                        # remove one-time datetime trigger after firing
                        try:
                            _store.watchdog.remove(trig)
                            _store.save()
                        except Exception:
                            pass
                elif trig.type == "interval":
                    # trig is IntervalTrigger
                    if time.time() - trig.last_triggered >= trig.interval_seconds:
                        _emit_trigger(trig.model_dump())
                        # update last_triggered
                        trig.last_triggered = time.time()
                        _store.save()
                elif trig.type == "py-eval":
                    try:
                        # evaluate; keep original behavior but catch exceptions
                        if eval(trig.eval_code):
                            _emit_trigger(trig.model_dump())
                    except Exception as e:
                        print(f"[trigger_manager] Error evaluating trigger {trig.id}: {e}")
                elif trig.type == "event":
                    if trig.event_name == "nimble_result" and trig.callback_id:
                        session = nimble.get_nimble_session(trig.callback_id)
                        if session and session.get("result") is not None:
                            _emit_trigger(trig.model_dump())
                            try:
                                _store.watchdog.remove(trig)
                                _store.save()
                            except Exception:
                                pass
                    else:
                        # for other events, just trigger and let backend-main decide if it matches
                        print("[trigger_manager] Event trigger fired:", trig.event_name, "with payload:", trig.payload)
                        _emit_trigger(trig.model_dump())
                        _store.watchdog.remove(trig) # remove event trigger after firing once
                        _store.save()
                elif trig.type == "nimble-reminder":
                    if not nimble.is_nimble_session_alive(trig.callback_id):
                        try:
                            _store.watchdog.remove(trig)
                            _store.save()
                        except Exception:
                            pass
                        continue
                    if time.time() - trig.last_triggered >= trig.interval_seconds:
                        _emit_trigger(trig.model_dump())
                        trig.last_triggered = time.time()
                        _store.save()
                elif trig.type == "nimble-expire":
                    if now >= trig.target:
                        _emit_trigger(trig.model_dump())
                        try:
                            _store.watchdog.remove(trig)
                            _store.save()
                        except Exception:
                            pass
                else:
                    # unknown type ignored
                    continue
            except Exception as e:
                print(f"[trigger_manager] Watchdog loop error for trigger {getattr(trig,'id',None)}: {e}")
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


def append_trigger(trigger: dict | str):
    """Append a new trigger to the store.

    Supported trigger types are 'datetime', 'interval', 'py-eval', 'event', 'nimble-reminder', and 'nimble-expire'.

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
        trigger (dict): The trigger to append.

    Raises:
        ValueError: If the trigger type is unsupported or invalid.
    """    
    if isinstance(trigger, str):
        try:
            trigger = json.loads(trigger)
        except Exception as e:
            print(f"[trigger_manager] Invalid trigger JSON string: {e}")
            raise
    trigger = _apply_append_filters(trigger)
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
        elif ttype == "nimble-reminder":
            t = NimbleRemindTrigger.model_validate(trigger)
        elif ttype == "nimble-expire":
            t = NimbleExpireTrigger.model_validate(trigger)
        else:
            raise ValueError(f"Unsupported trigger type: {ttype}")
    except Exception as e:
        print(f"[trigger_manager] Invalid trigger payload: {e}")
        raise
    
    # remove any existing with same id, then append & save
    try:
        _store.watchdog = [x for x in _store.watchdog if x.id != t.id]
        _store.watchdog.append(t)
        _store.save()
    except Exception as e:
        print(f"[trigger_manager] Failed to append trigger: {e}")
        raise


def delete_trigger(trigger_id: str):
    global _store
    before = len(_store.watchdog)
    _store.watchdog = [t for t in _store.watchdog if t.id != trigger_id]
    if len(_store.watchdog) != before:
        try:
            _store.save()
        except Exception as e:
            print(f"[trigger_manager] Failed to save after delete: {e}")


def get_trigger_information() -> str:
    # return formatted JSON of current store
    try:
        data = {"watchdog": [t.model_dump() for t in _store.watchdog]}
        return json.dumps(data, indent=4, ensure_ascii=False, default=str)
    except Exception as e:
        print(f"[trigger_manager] Failed to serialize triggers: {e}")
        return "{}"


def clear_triggers():
    global _store
    _store.watchdog.clear()
    try:
        _store.save()
    except Exception as e:
        print(f"[trigger_manager] Failed to save after clear: {e}")
def has_queue_task():
    return not trigger_queue.empty()
if __name__ == "__main__":
    append_trigger({
        "id": "CORE_HEARTBEAT",
        "type": "interval",
        "interval_seconds": 300,
        "recall_description": "核心心跳触发器，不要修改，每5分钟触发一次，用于Agent执行周期性任务或自我检查。"
    })
    print("Initial triggers:", get_trigger_information())
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
    print("Trigger watchdog thread started.")
    while True:
        trig = get_next_trigger(timeout=1.0)
        if trig:
            print("Trigger fired:", trig)
        else:
            print("No trigger fired in the last second.")