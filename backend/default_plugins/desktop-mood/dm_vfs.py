"""faustbot://desktop-mood/ —— 桌面感知数据的按域分层只读视图。

每个数据域两份表示：

- ``<group>/<group>.json``：精确值 + 源状态 + 采样间隔，给需要程序化判断的场景；
- ``<group>/<group>.md``：同一份数据的可读行，给模型与人看；
- ``overview.md``：每个源一行，最省 token 的入口。

数据全部现算自插件 store 里最近一轮采集的结果：**读节点不触发任何采集**（WMI/联网查询不能挂在
read 上阻塞）。要最新数据就 read/write ``refresh`` 节点，它和规则节点的 reload 一样有副作用。

不可用/被关闭的源必须如实说明原因，不伪造数值（仓库规则：不隐瞒错误）。
"""

from __future__ import annotations

import json
import time
from typing import Any

import dm_sources

ROOT = '/desktop-mood'
OVERVIEW_PATH = f'{ROOT}/overview.md'
REFRESH_PATH = f'{ROOT}/refresh'
RHYTHM_PATH = f'{ROOT}/narrative/rhythm.md'

READ_HINT = ('读法：overview.md 最省 token（每源一行）；要细节读该域的 .md；要精确值/状态读同域的 .json；'
             '要最新数据则 read/write faustbot://desktop-mood/refresh。')


def _hms(ts: float | int | None) -> str:
    return time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(int(ts or 0)))


def _age_text(report: dict[str, Any]) -> str:
    updated = int(report.get('updated_at') or 0)
    if not updated:
        return '尚无采集数据'
    return f'{_hms(updated)}（{max(0, int(time.time()) - updated)} 秒前）'


async def _collect_view(plugin: Any) -> tuple[dict[str, Any], dict[str, Any]]:
    """(感知报告, 最近一轮采集到的原始字段)。两者都不触发采集。"""
    report = await plugin.perception_report()
    observed: dict[str, Any] = {}
    if plugin.store is not None:
        raw = (plugin.store.snapshot() or {}).get('snapshot')
        if isinstance(raw, dict):
            observed = raw
    return report, observed


def _entries_of(report: dict[str, Any], group: str) -> list[dict[str, Any]]:
    return [entry for entry in (report.get('sources') or []) if str(entry.get('group') or '') == group]


def _state_of(entry: dict[str, Any]) -> str:
    """'off'（未启用）| 'ok' | 其它 = 不可用原因。"""
    if not entry.get('collecting'):
        return 'off'
    reason = entry.get('status')
    return str(reason) if reason else 'ok'


def _field_values(entry: dict[str, Any], observed: dict[str, Any]) -> list[tuple[str, Any]]:
    """该源本轮真正采到的字段（原始值）；未采到的字段不出现。"""
    values: list[tuple[str, Any]] = []
    for item in entry.get('fields') or ():
        field = str(item.get('field') or '')
        if field and field in observed:
            values.append((field, observed[field]))
    return values


def group_payload(registry: Any, report: dict[str, Any], observed: dict[str, Any], group: str) -> dict[str, Any]:
    sources: dict[str, Any] = {}
    for entry in _entries_of(report, group):
        source_id = str(entry.get('id') or '')
        fields = {field: value for field, value in _field_values(entry, observed)}
        sources[source_id] = {
            'label': entry.get('label'),
            'state': _state_of(entry),
            'cadence_sec': int(entry.get('cadence') or 0),
            'fields': fields,
        }
    return {
        'generated_at': _hms(report.get('updated_at')),
        'age_sec': max(0, int(time.time()) - int(report.get('updated_at') or 0)) if report.get('updated_at') else None,
        'group': group,
        'group_label': dm_sources.GROUP_LABELS.get(group, group),
        'sources': sources,
    }


def render_group_json(registry: Any, report: dict[str, Any], observed: dict[str, Any], group: str) -> str:
    return json.dumps(group_payload(registry, report, observed, group), ensure_ascii=False, indent=2)


def render_group_md(registry: Any, report: dict[str, Any], observed: dict[str, Any], group: str) -> str:
    note = next((item['note'] for item in dm_sources.GROUPS if item['id'] == group), '')
    lines = [f'# {dm_sources.GROUP_LABELS.get(group, group)} · {group}', '', f'> {note}',
             f'> 数据时间: {_age_text(report)}', '']
    for entry in _entries_of(report, group):
        state = _state_of(entry)
        label = str(entry.get('label') or entry.get('id'))
        if state == 'off':
            lines += [f'## {label}（未启用）', '']
            continue
        lines += [f'## {label}（不可用：{state}）' if state != 'ok' else f'## {label}', '']
        rendered = 0
        for field, _value in _field_values(entry, observed):
            text = registry.format_field(field, observed)
            if text is None:
                continue
            lines.append(f'- {registry.labels.get(field, field)}: {text}')
            rendered += 1
        if not rendered:
            lines.append('- （本轮没采到值）')
        lines.append('')
    lines.append(READ_HINT)
    return '\n'.join(lines)


def render_overview_md(registry: Any, report: dict[str, Any], observed: dict[str, Any]) -> str:
    lines = ['# 桌面上下文总览（faustbot://desktop-mood/）', '', f'数据时间: {_age_text(report)}']
    narrative = report.get('narrative')
    if narrative:
        lines.append(f'场景摘要: {narrative}')
    disturbed = bool(report.get('disturbed'))
    reasons = '、'.join(report.get('disturb_reasons') or [])
    lines.append(f'免打扰: {"是（" + reasons + "）" if disturbed else "否"}')
    lines.append('')
    for group in dm_sources.GROUP_IDS:
        entries = _entries_of(report, group)
        if not entries:
            continue
        lines.append(f'## {dm_sources.GROUP_LABELS.get(group, group)}')
        for entry in entries:
            state = _state_of(entry)
            label = str(entry.get('label') or entry.get('id'))
            if state == 'off':
                lines.append(f'- {label}: 未启用')
                continue
            values = _field_values(entry, observed)
            if state != 'ok':
                lines.append(f'- {label}: 不可用（{state}）')
            elif not values:
                lines.append(f'- {label}: 本轮没采到值')
            else:
                field, _value = values[0]
                lines.append(f'- {label}: {registry.format_field(field, observed)}')
        lines.append('')
    lines.append(READ_HINT)
    return '\n'.join(lines)


async def install(ctx: Any, plugin: Any) -> None:
    """把数据域视图挂到 faustbot://desktop-mood/ 下（symbolic 节点，读时现算）。"""
    async def _json(path: str, group: str) -> str:
        report, observed = await _collect_view(plugin)
        return render_group_json(plugin.registry, report, observed, group)

    async def _md(path: str, group: str) -> str:
        report, observed = await _collect_view(plugin)
        return render_group_md(plugin.registry, report, observed, group)

    async def _overview(_path: str) -> str:
        report, observed = await _collect_view(plugin)
        return render_overview_md(plugin.registry, report, observed)

    async def _refresh_read(_path: str) -> str:
        summary = await plugin.refresh_now()
        return f'已重新采集一次桌面环境。\n{summary}\n'

    async def _refresh_write(_node: Any, _content: Any) -> None:
        summary = await plugin.refresh_now()
        plugin.log.info('desktop-mood refresh write -> %s', summary.splitlines()[0] if summary else '')

    for group in dm_sources.GROUP_IDS:
        if not plugin.registry.sources_of(group):
            continue
        label = dm_sources.GROUP_LABELS.get(group, group)
        await ctx.vfs_write_symbolic(
            f'{ROOT}/{group}/{group}.json',
            lambda path, group=group: _json(path, group),
            should_be_included_in_search=False,
            description=f'{label}：精确值/源状态/采样间隔（JSON）',
        )
        await ctx.vfs_write_symbolic(
            f'{ROOT}/{group}/{group}.md',
            lambda path, group=group: _md(path, group),
            should_be_included_in_search=False,
            description=f'{label}：可读行（Markdown）',
        )

    await ctx.vfs_write_symbolic(
        OVERVIEW_PATH,
        _overview,
        should_be_included_in_search=False,
        description='桌面上下文总览：每个感知源一行，最省 token 的入口',
    )
    await ctx.vfs_write_symbolic(
        REFRESH_PATH,
        _refresh_read,
        should_be_included_in_search=False,
        description='读取或写入本节点 = 立即重新采集一次桌面环境',
    )
    await ctx.vfs_set_write_handler(REFRESH_PATH, _refresh_write)
