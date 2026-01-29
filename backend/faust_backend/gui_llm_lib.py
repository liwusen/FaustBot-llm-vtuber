import os
import sys
import json
import math
import time
import requests
import pyautogui
from PIL import Image
from io import BytesIO
import base64
os.chdir(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
try:
    import faust_backend.config_loader as conf
except ImportError:
    import config_loader as conf
# API 配置
API_KEY = conf.GUI_OPERATOR_LLM_KEY
API_URL = conf.GUI_OPERATOR_LLM_BASE

# 图像处理参数（API请求和坐标映射必须保持一致）
MAX_PIXELS = 1280 * 28 * 28  # 1003520
MIN_PIXELS = 4 * 28 * 28     # 3136

HEADERS = {
    "Authorization": f"{API_KEY}",
    "Content-Type": "application/json"
}

SYSTEM_PROMPT = """## 1. 核心角色 (Core Role)

你是一个顶级的AI视觉操作代理。你的任务是分析电脑屏幕截图，理解用户的指令，然后将任务分解为单一、精确的GUI原子操作。

## 2. [CRITICAL] JSON Schema & 绝对规则

你的输出**必须**是一个严格符合以下规则的JSON对象。**任何偏差都将导致失败**。

- **[R1] 严格的JSON**: 你的回复**必须**是且**只能是**一个JSON对象。禁止在JSON代码块前后添加任何文本、注释或解释。

- **[R2] 严格的Parameters结构**:`thought`对象的结构: "在这里用一句话简要描述你的思考过程。例如：用户想打开浏览器，我看到了桌面上的Chrome浏览器图标，所以下一步是点击它。"

- **[R3] 精确的Action值**: action字段的值**必须**是`## 3. 工具集`中定义的一个大写字符串（例如 "CLICK", "TYPE"），不允许有任何前导/后置空格或大小写变化。

- **[R4] 严格的Parameters结构**: parameters对象的结构**必须**与所选Action在`## 3. 工具集`中定义的模板**完全一致**。键名、值类型都必须精确匹配。

- **[R5] 坐标类型**: 所有坐标值（如CLICK的x和y）**必须**是整数类型，不能是字符串或浮点数，也不得是列表等其他Json结构,如果在`## 3. 工具集`指定了类型为<integer>，Json返回也必须是整数类型。
## 3. 工具集 (Available Actions)

### CLICK

- **功能**: 单击屏幕。

- **Parameters模板**:

{

"x": <integer>,

"y": <integer>,

"description": "<string, optional:  (可选) 一个简短的字符串，描述你点击的是什么，例如 "Chrome浏览器图标" 或 "登录按钮"。>"

}

### TYPE

- **功能**: 输入文本。

- **Parameters模板**:

{

"text": "<string>",

"needs_enter": <boolean>

}

### SCROLL

- **功能**: 滚动窗口。

- **Parameters模板**:

{

"direction": "<'up' or 'down'>",

"amount": "<'small', 'medium', or 'large'>"

}
### DOUBLE_CLICK
- **功能**: 双击屏幕。
- **Parameters模板**:
{

"x": <integer>,

"y": <integer>,

"description": "<string, optional:  (可选) 一个简短的字符串，描述你点击的是什么，例如 "Chrome浏览器图标" 或 "登录按钮"。>"

}
### RIGHT_CLICK
- **功能**: 右击屏幕。
- **Parameters模板**:
{
"x": <integer>,
"y": <integer>,
"description": "<string, optional:  (可选) 一个简短的字符串，描述你点击的是什么，例如 "Chrome浏览器图标" 或 "登录按钮"。>"
}

### KEY_PRESS

- **功能**: 按下功能键。

- **Parameters模板**:

{

"key": "<string: e.g., 'enter', 'esc', 'alt+f4'>"

}

### FINISH

- **功能**: 任务成功完成。

- **Parameters模板**:

{

"message": "<string: 总结任务完成情况>"

}

### FAILE

- **功能**: 任务无法完成。

- **Parameters模板**:

{

"reason": "<string: 清晰解释失败原因>"

}

## 4. 思维与决策框架

在生成每一步操作前，请严格遵循以下思考-验证流程：

目标分析: 用户的最终目标是什么？

屏幕观察 (Grounded Observation): 仔细分析截图。你的决策必须基于截图中存在的视觉证据。 如果你看不见某个元素，你就不能与它交互。

行动决策: 基于目标和可见的元素，选择最合适的工具。

构建输出：

a. 在thought字段中记录你的思考。

b. 选择一个action。

c. 精确复制该action的parameters模板，并填充值。

最终验证 (Self-Correction): 在输出前，最后检查一遍：

我的回复是纯粹的JSON吗？

action的值是否正确无误（大写、无空格）？

parameters的结构是否与模板100%一致？例如，对于CLICK，是否有独立的x和y键，并且它们的值都是整数？
## 5.输出示例
这是一个有效的JSON示例，仅供你参考格式：
{
  "thought": "用户的目标是打开Edge浏览器。根据截图，桌面上有一个Edge浏览器的图标，其位置 坐标大约为(170, 713)。因此，下一步操作应该是点击该图标来启动Edge浏览器。",
  "action": "CLICK",
  "parameters": {
    "x": 170,
    "y": 713
  }
}

"""


def get_response(image_url: str, instruction: str) -> str:
    """
    调用 API 获取模型响应。

    Args:
        image_url (str): 屏幕截图的 URL。
        instruction (str): 用户指令。

    Returns:
        str: 模型返回的内容。
    """
    data = {
        "model": conf.GUI_OPERATOR_LLM_MODEL,
        "messages": [
            {
                "role": "system",
                "content": SYSTEM_PROMPT
            },
            {
                "role": "user",
                "content": [
                    {
                        "type": "image_url",
                        "image_url": {
                            "url": image_url
                        },
                        "max_pixels": MAX_PIXELS,
                        "min_pixels": MIN_PIXELS
                    },
                    {
                        "type": "text",
                        "text": instruction
                    }
                ]
            }
        ]
    }

    response = requests.post(API_URL, headers=HEADERS, json=data)
    result = response.json()
    print("[Faust.backend.gui_llm_lib]API返回完整结果：", result)
    content = result["choices"][0]["message"]["content"]
    return content


def parse_json(json_output):
    lines = json_output.splitlines()
    for i, line in enumerate(lines):
        if line == "```json":
            json_output = "\n".join(lines[i + 1:])  # 删除 "```json"之前的所有内容
            json_output = json_output.split("```")[0]  # 删除 "```"之后的所有内容
            break  # 找到"```json"后退出循环
    response_dict = json.loads(json_output)
    return response_dict


def smart_size(image_url, point, factor=28, max_pixels=MAX_PIXELS, min_pixels=MIN_PIXELS):
    """
    param
      image_url: 图像url
      point: 包含 x, y 坐标的字典
      max_pixels：输入图像的最大像素值，超过此值则将图像的像素缩小至max_pixels内，与发起模型调用步骤设置的max_pixels值，应保持一致。
      min_pixels：输入图像的最小像素值，一般设置为默认值：4 * 28 * 28即可。
    return: 转换后的实际坐标 (abs_x, abs_y)
    """
    if image_url.startswith("http://") or image_url.startswith("https://"):
        response = requests.get(image_url)
        response.raise_for_status()
        image = Image.open(BytesIO(response.content))
    elif image_url.startswith("file://"):
        image=Image.open(image_url.replace("file://",""))
    # 获取图片的原始尺寸
    height = image.height
    width = image.width
    print(f"[Faust.backend.gui_llm_lib][DEBUG] 原始图片尺寸: {width} x {height}")
    print(f"[Faust.backend.gui_llm_lib][DEBUG] 模型输出坐标: x={point['x']}, y={point['y']}")

    # 将高度调整为factor的整数倍
    h_bar = round(height / factor) * factor
    # 将宽度调整为factor的整数倍
    w_bar = round(width / factor) * factor
    # 对图像进行缩放处理，调整像素的总数在范围[min_pixels,max_pixels]内
    if h_bar * w_bar > max_pixels:
        # 计算缩放因子beta，使得缩放后的图像总像素数不超过max_pixels
        beta = math.sqrt((height * width) / max_pixels)
        # 重新计算调整后的高度，确保为factor的整数倍
        h_bar = math.floor(height / beta / factor) * factor
        # 重新计算调整后的宽度，确保为factor的整数倍
        w_bar = math.floor(width / beta / factor) * factor
    elif h_bar * w_bar < min_pixels:
        # 计算缩放因子beta，使得缩放后的图像总像素数不低于min_pixels
        beta = math.sqrt(min_pixels / (height * width))
        # 重新计算调整后的高度，确保为factor的整数倍
        h_bar = math.ceil(height * beta / factor) * factor
        # 重新计算调整后的宽度，确保为factor的整数倍
        w_bar = math.ceil(width * beta / factor) * factor
    print(f"[Faust.backend.gui_llm_lib][DEBUG] 模型处理后的图片尺寸: {w_bar} x {h_bar}")

    abs_x = int(point["x"] / w_bar * width)
    abs_y = int(point["y"] / h_bar * height)
    print(f"[Faust.backend.gui_llm_lib][DEBUG] 映射后的实际坐标: x={abs_x}, y={abs_y}")
    return abs_x, abs_y


# 模拟滚动操作的默认幅度
SCROLL_AMOUNTS = {
    "small": 50,
    "medium": 200,
    "large": 500,
}


def execute_gui_action(action: str, parameters: dict, original_image_url: str):
    """
    根据模型输出的动作和参数执行GUI操作。

    Args:
        action (str): 模型输出的动作类型（如 "CLICK", "TYPE"）。
        parameters (dict): 动作的参数字典。
        original_image_url (str): 原始屏幕截图的URL，用于坐标映射。
    """
    action = action.strip()  # 去除前后空格
    print(f"[Faust.backend.gui_llm_lib]执行动作: {action}, 参数: {parameters}")

    if action == "CLICK":
        # 确保 'x' 和 'y' 存在并为数值类型
        if "x" not in parameters or "y" not in parameters:
            print("[Faust.backend.gui_llm_lib]错误: CLICK 动作缺少 'x' 或 'y' 坐标。")
            return "Error: CLICK action missing 'x' or 'y' coordinates."

        # 将模型输出的坐标映射到原始屏幕分辨率
        try:
            abs_x, abs_y = smart_size(original_image_url, parameters)
            pyautogui.click(abs_x, abs_y)
            print(f"[Faust.backend.gui_llm_lib]已点击坐标 ({abs_x}, {abs_y})")
            return "Success"
        except Exception as e:
            print(f"[Faust.backend.gui_llm_lib]坐标映射或点击失败: {e}")
            return f"Error: Coordinate mapping or click failed: {e}"
    elif action == "DOUBLE_CLICK":
        if 'x' not in parameters or 'y' not in parameters:
            print("[Faust.backend.gui_llm_lib]错误: DOUBLE_CLICK 动作缺少 'x' 或 'y' 坐标。")
            return "Error: DOUBLE_CLICK action missing 'x' or 'y' coordinates."
        try:
            abs_x, abs_y = smart_size(original_image_url, parameters)
            pyautogui.doubleClick(abs_x, abs_y)
            print(f"[Faust.backend.gui_llm_lib]已双击坐标 ({abs_x}, {abs_y})")
            return "Success"
        except Exception as e:
            print(f"[Faust.backend.gui_llm_lib]坐标映射或双击失败: {e}")
            return f"Error: Coordinate mapping or double click failed: {e}"
    elif action=="RIGHT_CLICK":
        if 'x' not in parameters or 'y' not in parameters:
            print("[Faust.backend.gui_llm_lib]错误: RIGHT_CLICK 动作缺少 'x' 或 'y' 坐标。")
            return "Error: RIGHT_CLICK action missing 'x' or 'y' coordinates."
        try:
            abs_x, abs_y = smart_size(original_image_url, parameters)
            pyautogui.rightClick(abs_x, abs_y)
            print(f"已右击坐标 ({abs_x}, {abs_y})")
            return "Success"
        except Exception as e:
            print(f"[Faust.backend.gui_llm_lib] 坐标映射或右击失败: {e}")
            return f"Error: Coordinate mapping or right click failed: {e}"
    elif action == "TYPE":
        if "text" not in parameters:
            print("错误: TYPE 动作缺少 'text' 参数。")
            return "Error: TYPE action missing 'text' parameter."

        text_to_type = parameters["text"]
        needs_enter = parameters.get("needs_enter", False)

        pyautogui.write(text_to_type)
        if needs_enter:
            pyautogui.press("enter")
        print(f"[Faust.backend.gui_llm_lib]已输入文本: '{text_to_type}', 是否按回车: {needs_enter}")
        return "Success"
    elif action == "SCROLL":
        if "direction" not in parameters or "amount" not in parameters:
            print("错误: SCROLL 动作缺少 'direction' 或 'amount' 参数。")
            return "Error: SCROLL action missing 'direction' or 'amount' parameter."

        direction = parameters["direction"].lower()
        amount_key = parameters["amount"].lower()

        scroll_value = SCROLL_AMOUNTS.get(amount_key, SCROLL_AMOUNTS["medium"])  # 默认中等

        if direction == "up":
            pyautogui.scroll(scroll_value)
            print(f"[Faust.backend.gui_llm_lib]已向上滚动 {scroll_value} 单位。")
        elif direction == "down":
            pyautogui.scroll(-scroll_value)  # pyautogui向下滚动需要负值
            print(f"[Faust.backend.gui_llm_lib]已向下滚动 {scroll_value} 单位。")
        else:
            print(f"[Faust.backend.gui_llm_lib]警告: 未知滚动方向: {direction}")

    elif action == "KEY_PRESS":
        if "key" not in parameters:
            print("[Faust.backend.gui_llm_lib]错误: KEY_PRESS 动作缺少 'key' 参数。")
            return "Error: KEY_PRESS action missing 'key' parameter."

        key_to_press = parameters["key"].lower()
        pyautogui.press(key_to_press)
        print(f"[Faust.backend.gui_llm_lib]已按下按键: {key_to_press}")
        return "Success"
    elif action == "FINISH":
        message = parameters.get("message", "任务已完成。")
        print(f"[Faust.backend.gui_llm_lib]任务完成: {message}")
        return "Success"
    elif action == "FAIL":
        reason = parameters.get("reason", "任务失败。")
        print(f"[Faust.backend.gui_llm_lib]任务失败: {reason}")
        return "Failure:Cannot Process Your Request"
    else:
        print(f"[Faust.backend.gui_llm_lib]警告: 收到未知动作类型: {action}")
        return f"Error: Unknown action type: {action}"

    # 模拟人类操作的延时，避免GUI操作过快
    time.sleep(0.75)  # 每次操作后等待1秒
def encode_image(image_path):
    with open(image_path, "rb") as image_file:
        return base64.b64encode(image_file.read()).decode("utf-8")
def gui_op(instruction: str)->str:
    #get screen shot
    screen_shot = pyautogui.screenshot()
    screen_shot_path = "current_screen.png"
    screen_shot.save(screen_shot_path)
    print(f"[Faust.backend.gui_llm_lib]已保存当前屏幕截图到 {screen_shot_path}")

    encoded_image = encode_image(screen_shot_path)


    original_image_url = f"data:image/png;base64,{encoded_image}"
    print("[Faust.backend.gui_llm_lib]调用大模型进行指令解析...")
    model_response = get_response(original_image_url, instruction)
    print("[Faust.backend.gui_llm_lib]大模型的回复：", model_response)
    result_str=""
    try:
        response_dict = parse_json(model_response)
        result_str+=f"解析结果: {response_dict}\n"
        action = response_dict["action"]
        parameters = response_dict["parameters"]
    except (ValueError, KeyError, json.JSONDecodeError) as e:
        print(f"处理模型响应失败: {e}")
        sys.exit(1)
    print("[Faust.backend.gui_llm_lib]解析后的动作和参数")
    print(f"[Faust.backend.gui_llm_lib]动作: {action}, 参数: {parameters}")
    result_str += "执行结果是: "+execute_gui_action(action, parameters, f"file://{screen_shot_path}")
    #os.remove(screen_shot_path)
    return result_str
if __name__ == "__main__":
    print(gui_op(input("[Faust.backend.gui_llm_lib]请输入您的指令: ")))
    