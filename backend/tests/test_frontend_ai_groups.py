"""AI 服务商页分组与配置键白名单的一致性守护。

AI 页的渲染完全由 `frontend/configer/src/config/constants.ts` 里的 `AI_GROUPS` 驱动：
它必须恰好覆盖 `AI_PUBLIC_KEYS ∪ AI_PRIVATE_KEYS`。只要漏掉一个键，那个配置项
就会从界面上悄悄消失（既不在 AI 页，也不在「高级」页——那两个键集本来就被排除）。
这里用静态解析守住这条约束，不需要运行前端。
"""

from __future__ import annotations

import re
from pathlib import Path

CONFIGER = Path(__file__).resolve().parents[2] / "frontend" / "configer" / "src"
CONSTANTS_TS = CONFIGER / "config" / "constants.ts"
AI_PAGE_VUE = CONFIGER / "modules" / "ai" / "AiModule.vue"
REGISTRY_TS = CONFIGER / "modules" / "registry.ts"


def _js_array_keys(text: str, var_name: str) -> list[str]:
    match = re.search(
        rf"(?:export\s+)?const\s+{var_name}\b[^=]*=\s*\[(.*?)\];",
        text,
        re.S,
    )
    assert match is not None, f"{var_name} 数组没找到"
    return re.findall(r'"([A-Za-z0-9_]+)"', match.group(1))


def _groups_block(text: str) -> str:
    match = re.search(r"// BEGIN:AI_GROUPS(.*?)// END:AI_GROUPS", text, re.S)
    assert match is not None, "AI_GROUPS 缺少 BEGIN/END 标记（静态测试靠它定位）"
    return match.group(1)


def _split_groups(block: str) -> list[str]:
    """按 `id: "..."` 切出每个分组对象的源码片段。"""
    starts = [m.start() for m in re.finditer(r'\bid:\s*"', block)]
    assert starts, "AI_GROUPS 里没有分组"
    return [block[a:b] for a, b in zip(starts, starts[1:] + [len(block)])]


def _row_objects(group_src: str) -> list[str]:
    return re.findall(r"\{[^{}]*?\bkey:\s*\"[A-Z0-9_]+\"[^{}]*?\}", group_src, re.S)


def test_ai_groups_covers_whitelist_exactly() -> None:
    text = CONSTANTS_TS.read_text(encoding="utf-8")
    public_keys = _js_array_keys(text, "AI_PUBLIC_KEYS")
    private_keys = _js_array_keys(text, "AI_PRIVATE_KEYS")
    whitelist = set(public_keys) | set(private_keys)
    assert whitelist, "AI 配置键白名单为空，测试失去意义"

    grouped: dict[str, str] = {}
    duplicates: list[str] = []
    for group_src in _split_groups(_groups_block(text)):
        for row in _row_objects(group_src):
            key = re.search(r'\bkey:\s*"([A-Z0-9_]+)"', row).group(1)
            if key in grouped:
                duplicates.append(key)
            grouped[key] = row

    missing = sorted(whitelist - set(grouped))
    unknown = sorted(set(grouped) - whitelist)
    assert not missing, f"这些配置键没有分组，界面上会消失: {missing}"
    assert not unknown, f"AI_GROUPS 里有白名单之外的键: {unknown}"
    assert not duplicates, f"同一个键被放进多个分组: {sorted(set(duplicates))}"


def test_ai_groups_scope_matches_key_category() -> None:
    text = CONSTANTS_TS.read_text(encoding="utf-8")
    public_keys = set(_js_array_keys(text, "AI_PUBLIC_KEYS"))
    private_keys = set(_js_array_keys(text, "AI_PRIVATE_KEYS"))

    for group_src in _split_groups(_groups_block(text)):
        for row in _row_objects(group_src):
            key = re.search(r'\bkey:\s*"([A-Z0-9_]+)"', row).group(1)
            is_private_row = bool(re.search(r'\bscope:\s*"private"', row))
            if key in private_keys:
                assert is_private_row, f"{key} 是私密配置，行定义必须带 scope: \"private\""
            elif key in public_keys:
                assert not is_private_row, f"{key} 是公共配置，不该标成 private"


def test_ai_groups_have_titles_and_descriptions() -> None:
    text = CONSTANTS_TS.read_text(encoding="utf-8")
    groups = _split_groups(_groups_block(text))
    assert len(groups) >= 5, "AI 页分组太少，布局回到一张大卡了"

    for group_src in groups:
        title = re.search(r'\btitle:\s*"([^"]*)"', group_src)
        desc = re.search(r'\bdesc:\s*"([^"]*)"', group_src)
        assert title and title.group(1).strip(), f"分组缺少标题: {group_src[:60]}"
        assert desc and desc.group(1).strip(), f"分组「{title.group(1)}」缺少一句话说明"

    providers = [g for g in groups if re.search(r'\bcustom:\s*"providers"', g)]
    assert len(providers) == 1, "「对话模型服务商」应当有且只有一个自定义分组"
    assert not _row_objects(providers[0]), "自定义分组自己渲染服务商/模型，不应再挂字段行"


def test_ai_page_wiring() -> None:
    """渲染入口接上：AI 模块组件存在、引用分组表，并在 registry 中注册。"""
    assert AI_PAGE_VUE.exists(), "modules/ai/AiModule.vue 缺失"
    page_text = AI_PAGE_VUE.read_text(encoding="utf-8")
    assert "AI_GROUPS" in page_text, "AiModule.vue 没有引用 AI_GROUPS"

    registry = REGISTRY_TS.read_text(encoding="utf-8")
    assert re.search(r"from\s+['\"]\./ai/AiModule\.vue['\"]", registry), "registry 没有导入 AiModule"
    assert re.search(r"\bai:\s*AiModule\b", registry), "registry 没有把 ai 映射到 AiModule"
