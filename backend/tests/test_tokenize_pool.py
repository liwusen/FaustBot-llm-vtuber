# backend/tests/test_tokenize_pool.py
import sys
from pathlib import Path

import pytest

BACKEND_ROOT = Path(__file__).resolve().parents[1]
if str(BACKEND_ROOT) not in sys.path:
    sys.path.insert(0, str(BACKEND_ROOT))

pytestmark = pytest.mark.asyncio(loop_scope="module")


async def test_jieba_tokenize_separates_words():
    from faust_backend.memory.tokenize_pool import jieba_tokenize
    toks = await jieba_tokenize("谢谢，请问LSTM是什么")
    assert "谢谢" in toks
    assert "LSTM" in toks
    assert "什么" in toks


async def test_jieba_tokenize_batch_order():
    from faust_backend.memory.tokenize_pool import jieba_tokenize, jieba_tokenize_batch
    res = await jieba_tokenize_batch(["你好", "记忆检索测试"])
    assert len(res) == 2
    assert res[0] == await jieba_tokenize("你好")
    assert res[1] == await jieba_tokenize("记忆检索测试")


def test_filter_info_tokens_drops_greetings_and_single_chars():
    from faust_backend.memory.stopwords import filter_info_tokens
    toks = ["你好", "谢谢", "请问", "LSTM", "是", "什么", "我"]
    out = filter_info_tokens(toks)
    assert out == ["LSTM"]


def test_filter_info_tokens_empty_for_pure_greeting():
    from faust_backend.memory.stopwords import filter_info_tokens
    assert filter_info_tokens(["你好", "嗯", "啊"]) == []
