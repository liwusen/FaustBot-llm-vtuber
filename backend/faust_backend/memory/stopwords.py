# backend/faust_backend/memory/stopwords.py
"""低信息熵过滤：从 jieba 分词结果中剔除寒暄/功能词，保留承载检索意图的 token。"""

INFO_STOPWORDS = frozenset({
    # 寒暄/礼貌
    "你好", "您好", "谢谢", "感谢", "再见", "晚安", "早上好", "下午好", "晚上好",
    "请问", "麻烦", "帮我", "帮忙", "辛苦", "不好意思",
    # 疑问/指代骨架
    "什么", "怎么", "怎样", "为什么", "哪里", "哪个", "哪些", "多少", "如何",
    "这个", "那个", "这些", "那些", "我们", "你们", "他们", "她们", "它们", "自己",
    # 功能/程度
    "可以", "就是", "还是", "已经", "应该", "需要", "想要", "觉得", "感觉",
    "一下", "一些", "一点", "没有", "不是", "不会", "不能", "非常", "真的",
    "然后", "所以", "但是", "如果", "因为", "而且", "或者", "以及", "关于",
    "现在", "今天", "明天", "昨天", "时候", "地方", "东西", "事情", "问题",
})


def filter_info_tokens(tokens: list[str], min_len: int = 2) -> list[str]:
    out: list[str] = []
    for t in tokens:
        s = str(t).strip()
        if len(s) < min_len or len(s) > 24:
            continue
        if s in INFO_STOPWORDS:
            continue
        if not any(ch.isalnum() for ch in s):
            continue
        out.append(s)
    return out
