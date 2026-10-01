"""量化加载配置（FaustBot 新增，上游 OmniJev 只有 bf16 路径）。

两个约束都踩过（实测）：
  * 跳过键是**前缀正则**匹配（transformers 的 ``should_convert_module``），视觉塔必须写真实路径
    ``model.visual``；写 ``visual`` 静默无效，视觉塔照样被量化。
  * 一旦传了 ``llm_int8_skip_modules``，transformers 的**默认跳过列表会被丢弃**，所以 ``lm_head``
    必须显式列上；否则 ``add_option_tokens()`` 的 ``resize_token_embeddings`` 会破坏被量化的
    lm_head，加载后第一次前向就 ``AssertionError: module.weight.shape[1] == 1``。

实测（RTX 5060 Laptop 8GB / Qwen3.5-4B）：nf4 峰值显存 4.26GB，nf4+视觉塔 bf16 4.71GB，int8 6.02GB。
"""

from __future__ import annotations

import torch
from transformers import BitsAndBytesConfig

#: 保持全精度的模块（前缀匹配）
SKIP_MODULES = ("model.visual", "lm_head")

#: 可选档位
KINDS = ("4bit", "8bit")


def config(kind: str) -> BitsAndBytesConfig:
    """按档位构造 ``BitsAndBytesConfig``；未知档位立即报错。"""
    kind = str(kind)
    if kind == "4bit":
        return BitsAndBytesConfig(
            load_in_4bit=True,
            bnb_4bit_quant_type="nf4",
            bnb_4bit_compute_dtype=torch.bfloat16,
            bnb_4bit_use_double_quant=True,
            llm_int8_skip_modules=list(SKIP_MODULES),
        )
    if kind == "8bit":
        return BitsAndBytesConfig(load_in_8bit=True, llm_int8_skip_modules=list(SKIP_MODULES))
    raise ValueError(f"不支持的量化档位: {kind!r}（可选: {', '.join(KINDS)}）")
