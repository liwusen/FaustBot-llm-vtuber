"""OmniJev 推理代码（vendored fork）。

上游: https://github.com/tinnel123666888/OmniJev  commit 14dbec4f71e194852c8d7b88ab36ef639493f400
许可: Apache-2.0（见同目录 LICENSE）。本目录只保留推理所需模块：
      infer / records / head / v04 / branch / panels / fast_kernels / video
      上游的 action.py、action_infer.py、templates.py 是训练与 Action 专家侧代码，未纳入。

改动（除此之外与上游逐字节一致）：
  * infer.py     MSO1(..., quant=...) → 4bit/8bit 量化加载（FaustBot 在 8GB 显存上跑 4B 需要）
  * q4.py        新增：量化配置（本仓库新增文件，上游没有）
  * __init__.py  本文件（上游为空）
"""
