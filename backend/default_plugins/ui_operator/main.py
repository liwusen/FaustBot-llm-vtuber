from __future__ import annotations

import json
import threading
from typing import Any

import numpy as np
import pyautogui

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from faust_backend.plugin_system import PluginContext, PluginManifest, ToolSpec


_OCR_READER = None
_OCR_READER_LOCK = threading.Lock()
_LAST_OCR_ITEMS: list[dict[str, Any]] = []
_LAST_OCR_LOCK = threading.Lock()


def _parse_langs(raw: Any, fallback: list[str]) -> list[str]:
    if isinstance(raw, list):
        langs = [str(x).strip() for x in raw if str(x).strip()]
        return langs or fallback
    if isinstance(raw, str):
        text = raw.strip()
        if not text:
            return fallback
        try:
            parsed = json.loads(text)
            if isinstance(parsed, list):
                langs = [str(x).strip() for x in parsed if str(x).strip()]
                return langs or fallback
        except Exception:
            pass
        if "," in text:
            langs = [x.strip() for x in text.split(",") if x.strip()]
            return langs or fallback
        return [text]
    return fallback


def _clamp01(v: float) -> float:
    return max(0.0, min(1.0, float(v)))


def _safe_float(v: Any, default: float) -> float:
    try:
        return float(v)
    except Exception:
        return default


def _safe_int(v: Any, default: int) -> int:
    try:
        return int(v)
    except Exception:
        return default


def _load_ocr_reader(langs: list[str], gpu: bool):
    global _OCR_READER
    with _OCR_READER_LOCK:
        if _OCR_READER is not None:
            return _OCR_READER
        import easyocr  # lazy import，避免未安装时阻塞插件加载

        _OCR_READER = easyocr.Reader(langs, gpu=gpu)
        return _OCR_READER


def _norm_to_pixel(x: float, y: float) -> tuple[int, int]:
    width, height = pyautogui.size()
    nx = _clamp01(x)
    ny = _clamp01(y)
    px = int(round(nx * max(1, width - 1)))
    py = int(round(ny * max(1, height - 1)))
    return px, py


def _pixel_to_norm(x: float, y: float) -> tuple[float, float]:
    width, height = pyautogui.size()
    nx = 0.0 if width <= 0 else float(x) / float(width)
    ny = 0.0 if height <= 0 else float(y) / float(height)
    return _clamp01(nx), _clamp01(ny)


def _extract_center_norm_from_box(box: Any) -> tuple[float, float] | None:
    # easyocr box: [[x1,y1],[x2,y2],[x3,y3],[x4,y4]]
    if not isinstance(box, (list, tuple)) or len(box) < 4:
        return None
    xs: list[float] = []
    ys: list[float] = []
    for point in box:
        if not isinstance(point, (list, tuple)) or len(point) < 2:
            continue
        xs.append(float(point[0]))
        ys.append(float(point[1]))
    if not xs or not ys:
        return None
    cx = (min(xs) + max(xs)) / 2.0
    cy = (min(ys) + max(ys)) / 2.0
    return _pixel_to_norm(cx, cy)


def _set_last_ocr_items(items: list[dict[str, Any]]) -> None:
    global _LAST_OCR_ITEMS
    with _LAST_OCR_LOCK:
        _LAST_OCR_ITEMS = list(items)


def _get_last_ocr_item(ocr_id: int) -> dict[str, Any] | None:
    with _LAST_OCR_LOCK:
        for item in _LAST_OCR_ITEMS:
            if _safe_int(item.get("id"), -1) == ocr_id:
                return item
    return None


class Plugin:
    manifest = PluginManifest(
        plugin_id="ui_operator",
        name="UI Operator Plugin",
        version="1.0.0",
        description="Screen OCR and direct UI control tools based on pyautogui and easyocr",
        author="faust",
        homepage="",
        enabled=False,
        permissions=[
            "tool:ui-ocr",
            "tool:ui-click",
            "tool:ui-right-click",
            "tool:ui-scroll",
            "tool:ui-keyboard",
        ],
        priority=240,
    )

    def startup(self, ctx: PluginContext) -> None:
        ctx.register_config(
            """
            OCR_LANGS:json:OCR语言列表=["ch_sim","en"]
            OCR_GPU:bool:OCR启用GPU=false
            OCR_MIN_CONF:float:OCR最小置信度=0.3
            PYAUTOGUI_PAUSE:float:每次GUI动作间隔秒数=0.05
            PYAUTOGUI_FAILSAFE:bool:启用鼠标角落紧急停止=true
            DEFAULT_TYPE_INTERVAL:float:默认打字间隔秒数=0.02
            SCROLL_STEP:int:滚轮每档默认步长=300
            """
        )

    def on_load(self, ctx: PluginContext) -> None:
        pass

    def on_unload(self, ctx: PluginContext) -> None:
        pass

    def register_middlewares(self, ctx: PluginContext):
        return []

    def register_tools(self, ctx: PluginContext):
        def _apply_pyautogui_runtime_settings() -> None:
            pyautogui.PAUSE = _safe_float(ctx.get_config("PYAUTOGUI_PAUSE", 0.05), 0.05)
            pyautogui.FAILSAFE = bool(ctx.get_config("PYAUTOGUI_FAILSAFE", True))

        def _resolve_target(ocr_id: int | None, x: float | None, y: float | None) -> tuple[float, float] | None:
            if ocr_id is not None and ocr_id > 0:
                item = _get_last_ocr_item(ocr_id)
                if item is None:
                    return None
                pos = item.get("pos") or []
                if not isinstance(pos, (list, tuple)) or len(pos) < 2:
                    return None
                return _clamp01(_safe_float(pos[0], 0.0)), _clamp01(_safe_float(pos[1], 0.0))

            if x is None or y is None:
                return None
            return _clamp01(_safe_float(x, 0.0)), _clamp01(_safe_float(y, 0.0))

        @tool
        def screenOCRTool(lang_list_json: str = "") -> str:
            """
            Description:
                对当前屏幕进行 OCR 识别，返回文本及归一化坐标。
            Args:
                lang_list_json (str): OCR语言列表JSON字符串，可选。示例: ["ch_sim","en"]
            Returns:
                str: JSON字符串，格式:
                     {"res":[{"id":1,"text":"Hello","pos":[0.5,0.3]}, ...]}
            """
            try:
                _apply_pyautogui_runtime_settings()
                cfg_langs = ctx.get_config("OCR_LANGS", ["ch_sim", "en"])
                langs = _parse_langs(lang_list_json, _parse_langs(cfg_langs, ["ch_sim", "en"]))
                use_gpu = bool(ctx.get_config("OCR_GPU", False))
                min_conf = _safe_float(ctx.get_config("OCR_MIN_CONF", 0.3), 0.3)

                reader = _load_ocr_reader(langs=langs, gpu=use_gpu)
                screenshot = pyautogui.screenshot()
                screenshot_array = np.array(screenshot)
                raw_results = reader.readtext(screenshot_array)

                out_items: list[dict[str, Any]] = []
                seq = 1
                for item in raw_results:
                    if not isinstance(item, (list, tuple)) or len(item) < 3:
                        continue
                    box, text, conf = item[0], item[1], item[2]
                    confidence = _safe_float(conf, 0.0)
                    if confidence < min_conf:
                        continue
                    center = _extract_center_norm_from_box(box)
                    if center is None:
                        continue
                    out_items.append(
                        {
                            "id": seq,
                            "text": str(text),
                            "pos": [round(center[0], 6), round(center[1], 6)],
                        }
                    )
                    seq += 1

                _set_last_ocr_items(out_items)
                return json.dumps({"res": out_items}, ensure_ascii=False)
            except Exception as e:
                print(f"OCR execution failed: {str(e)}")
                return json.dumps({"error": f"OCR执行失败: {str(e)}"}, ensure_ascii=False)

        @tool
        def screenClickTool(
            ocr_id: int = 0,
            x: float = -1,
            y: float = -1,
            clicks: int = 1,
        ) -> str:
            """
            Description:
                执行左键点击。支持两种方式：
                1) 通过 ocr_id 点击最近一次 OCR 识别到的文本；
                2) 通过归一化坐标 x/y 点击。
            Args:
                ocr_id (int): OCR结果中的id，>0时优先使用。
                x (float): 归一化横坐标(0~1)。
                y (float): 归一化纵坐标(0~1)。
                clicks (int): 点击次数。
            Returns:
                str: 操作结果。
            """
            try:
                _apply_pyautogui_runtime_settings()
                target = _resolve_target(ocr_id if ocr_id > 0 else None, x if x >= 0 else None, y if y >= 0 else None)
                if target is None:
                    return "点击失败: 请提供有效的 ocr_id，或同时提供 x/y 归一化坐标。"
                px, py = _norm_to_pixel(target[0], target[1])
                pyautogui.click(px, py, clicks=max(1, _safe_int(clicks, 1)), button="left")
                return f"已左键点击: norm=({target[0]:.4f},{target[1]:.4f}), pixel=({px},{py})"
            except Exception as e:
                return f"点击失败: {str(e)}"

        @tool
        def screenRightClickTool(
            ocr_id: int = 0,
            x: float = -1,
            y: float = -1,
        ) -> str:
            """
            Description:
                执行右键点击。支持通过 ocr_id 或归一化坐标点击。
            Args:
                ocr_id (int): OCR结果中的id，>0时优先使用。
                x (float): 归一化横坐标(0~1)。
                y (float): 归一化纵坐标(0~1)。
            Returns:
                str: 操作结果。
            """
            try:
                _apply_pyautogui_runtime_settings()
                target = _resolve_target(ocr_id if ocr_id > 0 else None, x if x >= 0 else None, y if y >= 0 else None)
                if target is None:
                    return "右键失败: 请提供有效的 ocr_id，或同时提供 x/y 归一化坐标。"
                px, py = _norm_to_pixel(target[0], target[1])
                pyautogui.click(px, py, button="right")
                return f"已右键点击: norm=({target[0]:.4f},{target[1]:.4f}), pixel=({px},{py})"
            except Exception as e:
                return f"右键失败: {str(e)}"

        @tool
        def screenScrollTool(direction: str = "down", steps: int = 1, x: float = -1, y: float = -1) -> str:
            """
            Description:
                执行滚轮操作（上/下）。可选指定归一化坐标，将鼠标移动到目标位置后滚动。
            Args:
                direction (str): up/down
                steps (int): 滚动档数（正整数）
                x (float): 可选，归一化横坐标(0~1)
                y (float): 可选，归一化纵坐标(0~1)
            Returns:
                str: 操作结果。
            """
            try:
                _apply_pyautogui_runtime_settings()
                step_count = max(1, _safe_int(steps, 1))
                base = max(1, _safe_int(ctx.get_config("SCROLL_STEP", 300), 300))
                amount = base * step_count

                d = str(direction or "down").strip().lower()
                if d not in {"up", "down"}:
                    return "滚轮失败: direction 仅支持 up/down。"
                signed = amount if d == "up" else -amount

                if x >= 0 and y >= 0:
                    nx, ny = _clamp01(_safe_float(x, 0.0)), _clamp01(_safe_float(y, 0.0))
                    px, py = _norm_to_pixel(nx, ny)
                    pyautogui.moveTo(px, py)
                    pyautogui.scroll(signed)
                    return f"已滚动: direction={d}, amount={amount}, at norm=({nx:.4f},{ny:.4f})"

                pyautogui.scroll(signed)
                return f"已滚动: direction={d}, amount={amount}"
            except Exception as e:
                return f"滚轮失败: {str(e)}"

        @tool
        def screenTypeTool(text: str, press_enter: bool = False, interval: float = -1) -> str:
            """
            Description:
                键盘输入文本，可选回车。
            Args:
                text (str): 要输入的文本。
                press_enter (bool): 输入后是否按下回车。
                interval (float): 键入字符间隔秒数，<0时使用插件默认配置。
            Returns:
                str: 操作结果。
            """
            try:
                _apply_pyautogui_runtime_settings()
                default_interval = _safe_float(ctx.get_config("DEFAULT_TYPE_INTERVAL", 0.02), 0.02)
                key_interval = default_interval if interval < 0 else max(0.0, _safe_float(interval, default_interval))
                pyautogui.write(str(text), interval=key_interval)
                if bool(press_enter):
                    pyautogui.press("enter")
                return f"已输入文本，长度={len(str(text))}"
            except Exception as e:
                return f"键盘输入失败: {str(e)}"

        @tool
        def screenKeyPressTool(key: str) -> str:
            """
            Description:
                按下一个按键。
            Args:
                key (str): 按键名，如 enter / esc / tab / f5。
            Returns:
                str: 操作结果。
            """
            try:
                _apply_pyautogui_runtime_settings()
                k = str(key or "").strip().lower()
                if not k:
                    return "按键失败: key 不能为空。"
                pyautogui.press(k)
                return f"已按键: {k}"
            except Exception as e:
                return f"按键失败: {str(e)}"

        @tool
        def screenHotkeyTool(keys_json: str) -> str:
            """
            Description:
                执行组合键。
            Args:
                keys_json (str): JSON数组字符串，如 ["ctrl", "c"]。
            Returns:
                str: 操作结果。
            """
            try:
                _apply_pyautogui_runtime_settings()
                keys = json.loads(keys_json)
                if not isinstance(keys, list) or not keys:
                    return "组合键失败: keys_json 必须是非空JSON数组。"
                seq = [str(k).strip().lower() for k in keys if str(k).strip()]
                if not seq:
                    return "组合键失败: 未解析出有效按键。"
                pyautogui.hotkey(*seq)
                return f"已执行组合键: {'+'.join(seq)}"
            except Exception as e:
                return f"组合键失败: {str(e)}"

        return [
            ToolSpec(name="screenOCRTool", tool=screenOCRTool, enabled_by_default=True, description=screenOCRTool.__doc__),
            ToolSpec(name="screenClickTool", tool=screenClickTool, enabled_by_default=True, description=screenClickTool.__doc__),
            ToolSpec(name="screenRightClickTool", tool=screenRightClickTool, enabled_by_default=True, description=screenRightClickTool.__doc__),
            ToolSpec(name="screenScrollTool", tool=screenScrollTool, enabled_by_default=True, description=screenScrollTool.__doc__),
            ToolSpec(name="screenTypeTool", tool=screenTypeTool, enabled_by_default=True, description=screenTypeTool.__doc__),
            ToolSpec(name="screenKeyPressTool", tool=screenKeyPressTool, enabled_by_default=True, description=screenKeyPressTool.__doc__),
            ToolSpec(name="screenHotkeyTool", tool=screenHotkeyTool, enabled_by_default=True, description=screenHotkeyTool.__doc__),
        ]

    def health_check(self) -> dict[str, Any]:
        return {"status": "ok", "plugin": "ui_operator"}

    def Heartbeat(self, ctx: PluginContext) -> None:
        pass


def get_plugin() -> Plugin:
    return Plugin()
