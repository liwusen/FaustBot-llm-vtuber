# UI Operator Plugin

该插件为 Agent 提供直接屏幕操作能力，基于 `pyautogui` 与 `easyocr`。

## 提供工具

- `screenOCRTool`
- `screenClickTool`
- `screenRightClickTool`
- `screenScrollTool`
- `screenTypeTool`
- `screenKeyPressTool`
- `screenHotkeyTool`

## OCR 返回格式

`screenOCRTool` 返回 JSON 字符串，结构如下：

```json
{
  "res": [
    {
      "id": 1,
      "text": "Hello World",
      "pos": [0.5, 0.3]
    }
  ]
}
```

- `pos` 为归一化坐标，范围 `[0,1]`。
- 点击工具支持：
  - 传 `ocr_id` 点击 OCR 项
  - 传 `x/y` 按归一化坐标点击
