"""B 站工具插件：搜索 / 视频信息 / 三连互动。

- 使用 B 站公开 Web API（requests），不依赖 bilibili_api。
- 搜索与视频信息无需登录；点赞/投币/收藏需要配置 SESSDATA cookie。
- 搜索结果会缓存到 VFS（faustbot://plugins/bilibili_tools/search-<关键词>.md）。
"""
from __future__ import annotations

import re
import time
from typing import Any

import requests

try:
    from langchain.tools import tool
except Exception:
    def tool(func):
        return func

from faust_backend.logger import get_logger
from faust_backend.plugin_system import FaustPlugin, PluginContext, ToolSpec, hookimpl

log = get_logger("faust.plugin.bilibili-tools")

_PLUGIN: "Plugin | None" = None

SEARCH_API = "https://api.bilibili.com/x/web-interface/search/type"
VIEW_API = "https://api.bilibili.com/x/web-interface/view"
LIKE_API = "https://api.bilibili.com/x/web-interface/archive/like"
COIN_API = "https://api.bilibili.com/x/web-interface/coin/add"
NAV_API = "https://api.bilibili.com/x/web-interface/nav"
FAV_FOLDER_API = "https://api.bilibili.com/x/v3/fav/folder/created/list-all"
FAV_DEAL_API = "https://api.bilibili.com/x/v3/fav/resource/deal"

MAX_RESULT_CHARS = 800
REQUEST_TIMEOUT = 10.0
SEARCH_RESULT_LIMIT = 10

_DEFAULT_COOKIE_HINT = (
    "B 站 cookie 已过期或未登录，请更新 SESSDATA（如需三连请一并提供 bili_jct）配置。"
)
_NO_COOKIE_MSG = (
    "未配置 SESSDATA cookie，互动功能（点赞/投币/收藏）不可用。"
    "请先在插件配置中填写 SESSDATA（可粘贴完整 cookie 字符串），搜索与视频信息功能仍可使用。"
)

# 需要 csrf 校验（即 bili_jct cookie）或登录态的常见错误码
_COOKIE_ERROR_CODES = (-101, -400, -111, -658)
_RISK_CONTROL_CODES = (-412, -352)

_UA = (
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
    "(KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36"
)

README_CONTENT = """# Bilibili Tools（bilibili_tools）

提供 B 站搜索 / 视频信息 / 三连互动工具（基于公开 Web API，requests 直连，无额外依赖）。

## 工具
- `bilibili_search(keyword, page=1)`：搜索视频，返回标题 / UP主 / bvid / 播放量（结果已截断，最多 800 字）。
- `bilibili_video_info(bvid)`：返回标题 / 简介 / UP主 / 分区 / 发布时间。
- `bilibili_three_actions(bvid, like=True, coin=True, favorite=True)`：点赞 / 投币 / 收藏，需要配置 SESSDATA cookie。

## 配置
- `SESSDATA`：B 站登录后的 SESSDATA 值；也可以直接粘贴完整 cookie 字符串（如 `SESSDATA=xxx; bili_jct=yyy`），
  其中的 `bili_jct` 会作为点赞/投币/收藏接口的 csrf 参数。留空时互动功能不可用，搜索与视频信息仍可用。
- `cookie_expired_hint`：接口返回未登录/过期（code -101/-400/-111 等）时附加的提示文案。

## 数据缓存
搜索成功的结果会写入 `faustbot://plugins/bilibili_tools/search-<关键词>.md`。
需要近期搜索记录时可直接读取该路径，避免重复调用搜索 API。
"""


def _strip_html(text: str) -> str:
    """去掉 B 站搜索标题里的 <em class="keyword"> 等标签并反转义。"""
    text = re.sub(r"<[^>]+>", "", text or "")
    return (
        text.replace("&amp;", "&")
        .replace("&lt;", "<")
        .replace("&gt;", ">")
        .replace("&quot;", '"')
        .replace("&#39;", "'")
        .replace("&nbsp;", " ")
    )


def _truncate(text: str, limit: int) -> str:
    text = (text or "").strip()
    if len(text) <= limit:
        return text
    return text[: limit - 1].rstrip() + "…"


def _bili_headers() -> dict[str, str]:
    return {
        "User-Agent": _UA,
        "Referer": "https://www.bilibili.com/",
        "Accept": "application/json, text/plain, */*",
    }


def _parse_cookie_config(raw: str) -> tuple[dict[str, str], str]:
    """解析 SESSDATA 配置为 cookie 字典 + csrf。

    支持两种写法：
    - 仅 SESSDATA 值（如 "abc123..."）
    - 完整 cookie 字符串（如 "SESSDATA=abc; bili_jct=xyz"），其中 bili_jct 用作 csrf。
    """
    raw = (raw or "").strip()
    if not raw:
        return {}, ""
    if not raw.startswith("SESSDATA="):
        # 裸 SESSDATA 值：值本身是 base64，常含 '='（如 abc=def），
        # 不能仅凭 "=" 区分裸值与完整 cookie 串，必须要求以 "SESSDATA=" 开头才走完整解析。
        return {"SESSDATA": raw}, ""
    pairs: dict[str, str] = {}
    for part in raw.split(";"):
        part = part.strip()
        if not part or "=" not in part:
            continue
        key, _, value = part.partition("=")
        pairs[key.strip()] = value.strip()
    return pairs, pairs.get("bili_jct", "")


class Plugin(FaustPlugin):
    def __init__(self) -> None:
        self.ctx: PluginContext | None = None

    def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        ctx.register_config(
            [
                {
                    "key": "SESSDATA",
                    "type": "str",
                    "label": "B 站 SESSDATA cookie（可粘贴完整 cookie 字符串，留空则互动功能不可用）",
                    "default": "",
                },
                {
                    "key": "cookie_expired_hint",
                    "type": "str",
                    "label": "cookie 失效提示文案",
                    "default": _DEFAULT_COOKIE_HINT,
                },
            ]
        )
        try:
            ctx.vfs_write("/plugins/bilibili_tools/README.md", README_CONTENT)
        except Exception as exc:
            log.warning("bilibili_tools vfs README write failed: %s", exc)

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        log.info("Bilibili Tools plugin loaded")
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    # ── 内部工具 ──

    def _cookie_expired_hint(self) -> str:
        if self.ctx is None:
            return _DEFAULT_COOKIE_HINT
        return str(self.ctx.get_config("cookie_expired_hint", _DEFAULT_COOKIE_HINT) or _DEFAULT_COOKIE_HINT)

    def _api_error_text(self, data: dict[str, Any]) -> str:
        code = data.get("code")
        message = str(data.get("message") or "未知错误")
        if code in _COOKIE_ERROR_CODES:
            return f"code={code} {message}。{self._cookie_expired_hint()}"
        if code in _RISK_CONTROL_CODES:
            return f"code={code} 请求被风控拦截，请稍后重试或更换网络环境。"
        return f"code={code} {message}"

    def _check_api_error(self, data: dict[str, Any]) -> str | None:
        if data.get("code") == 0:
            return None
        return self._api_error_text(data)

    def _require_cookie(self) -> tuple[dict[str, str], str, str | None]:
        raw = ""
        if self.ctx is not None:
            raw = str(self.ctx.get_config("SESSDATA", "") or "")
        if not raw.strip():
            return {}, "", _NO_COOKIE_MSG
        cookies, csrf = _parse_cookie_config(raw)
        return cookies, csrf, None

    def _mutation_result(self, action: str, resp: requests.Response) -> str:
        if resp.status_code != 200:
            return f"{action}失败: HTTP {resp.status_code}"
        try:
            data = resp.json()
        except ValueError:
            return f"{action}失败: 响应解析失败 (HTTP {resp.status_code})"
        if data.get("code") == 0:
            return f"{action}成功。"
        return f"{action}失败: {self._api_error_text(data)}"

    def _cache_search(self, keyword: str, text: str) -> None:
        ctx = self.ctx
        if ctx is None:
            return
        safe = re.sub(r"[^\w\u4e00-\u9fff-]+", "_", keyword)[:40] or "keyword"
        try:
            ctx.vfs_write(
                f"/plugins/bilibili_tools/search-{safe}.md",
                f"# B 站搜索：{keyword}\n\n{text}\n",
            )
        except Exception as exc:
            log.warning("bilibili_tools vfs cache write failed: %s", exc)

    # ── 工具实现 ──

    def search_videos(self, keyword: str, page: int = 1) -> str:
        keyword = (keyword or "").strip()
        if not keyword:
            return "搜索关键词为空。"
        try:
            page = max(1, int(page))
        except (TypeError, ValueError):
            page = 1
        params = {"search_type": "video", "keyword": keyword, "page": page}
        try:
            resp = requests.get(
                SEARCH_API, params=params, headers=_bili_headers(), timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            log.error("bilibili search request failed: %s", exc)
            return f"B 站搜索请求失败（网络错误）: {exc}"
        if resp.status_code != 200:
            return f"B 站搜索 API HTTP 错误: {resp.status_code}"
        try:
            data = resp.json()
        except ValueError:
            return f"B 站搜索响应解析失败 (HTTP {resp.status_code})"
        err = self._check_api_error(data)
        if err:
            return f"B 站搜索失败: {err}"
        items = (data.get("data") or {}).get("result") or []
        lines: list[str] = []
        for item in items[:SEARCH_RESULT_LIMIT]:
            if item.get("type") and item.get("type") != "video":
                continue
            title = _strip_html(str(item.get("title") or "无标题"))
            author = str(item.get("author") or "未知UP主")
            bvid = str(item.get("bvid") or "")
            play = item.get("play")
            if play is None:
                play = item.get("play_count") or 0
            lines.append(
                f"- {title} | UP主: {author} | 播放: {play} | https://www.bilibili.com/video/{bvid}"
            )
        if not lines:
            return "未找到相关视频。"
        text = "\n".join(lines)
        self._cache_search(keyword, text)
        return _truncate(text, MAX_RESULT_CHARS)

    def video_info(self, bvid: str) -> str:
        bvid = (bvid or "").strip()
        if not bvid:
            return "缺少 bvid 参数。"
        params = {"bvid": bvid}
        try:
            resp = requests.get(
                VIEW_API, params=params, headers=_bili_headers(), timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            log.error("bilibili video info request failed: %s", exc)
            return f"B 站视频信息请求失败（网络错误）: {exc}"
        if resp.status_code != 200:
            return f"B 站视频信息 API HTTP 错误: {resp.status_code}"
        try:
            data = resp.json()
        except ValueError:
            return f"B 站视频信息响应解析失败 (HTTP {resp.status_code})"
        err = self._check_api_error(data)
        if err:
            return f"B 站视频信息获取失败: {err}"
        d = data.get("data") or {}
        if not d:
            return "未找到该视频。"
        owner = d.get("owner") or {}
        stat = d.get("stat") or {}
        pubdate = d.get("pubdate") or 0
        pub_str = time.strftime("%Y-%m-%d", time.localtime(pubdate)) if pubdate else "未知"
        lines = [
            f"标题: {d.get('title') or '未知'}",
            f"UP主: {owner.get('name') or '未知'}",
            f"分区: {d.get('tname') or '未知'}",
            f"发布时间: {pub_str}",
            f"播放: {stat.get('view') or 0} / 点赞: {stat.get('like') or 0} / 弹幕: {stat.get('danmaku') or 0}",
            f"简介: {d.get('desc') or '（无）'}",
            f"链接: https://www.bilibili.com/video/{bvid}",
        ]
        return "\n".join(lines)

    def three_actions(
        self, bvid: str, like: bool = True, coin: bool = True, favorite: bool = True
    ) -> str:
        bvid = (bvid or "").strip()
        if not bvid:
            return "缺少 bvid 参数。"
        cookies, csrf, err = self._require_cookie()
        if err:
            return err
        results: list[str] = []
        if like:
            results.append(self._do_like(bvid, cookies, csrf))
        if coin:
            results.append(self._do_coin(bvid, cookies, csrf, select_like=bool(like)))
        if favorite:
            results.append(self._do_favorite(bvid, cookies, csrf))
        if not results:
            return "没有需要执行的操作（like/coin/favorite 均为 False）。"
        return "\n".join(results)

    def _do_like(self, bvid: str, cookies: dict[str, str], csrf: str) -> str:
        try:
            resp = requests.post(
                LIKE_API,
                data={"bvid": bvid, "like": 1, "csrf": csrf},
                cookies=cookies,
                headers=_bili_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.error("bilibili like request failed: %s", exc)
            return f"点赞失败（网络错误）: {exc}"
        return self._mutation_result("点赞", resp)

    def _do_coin(self, bvid: str, cookies: dict[str, str], csrf: str, select_like: bool) -> str:
        try:
            resp = requests.post(
                COIN_API,
                data={
                    "bvid": bvid,
                    "multiply": 1,
                    "select_like": 1 if select_like else 0,
                    "csrf": csrf,
                },
                cookies=cookies,
                headers=_bili_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.error("bilibili coin request failed: %s", exc)
            return f"投币失败（网络错误）: {exc}"
        return self._mutation_result("投币", resp)

    def _do_favorite(self, bvid: str, cookies: dict[str, str], csrf: str) -> str:
        fid, err = self._resolve_default_fav_folder(cookies)
        if err:
            return f"收藏失败: {err}"
        try:
            resp = requests.post(
                FAV_DEAL_API,
                data={
                    "rid": bvid,
                    "type": 2,
                    "add_media_ids": fid,
                    "del_media_ids": "",
                    "csrf": csrf,
                },
                cookies=cookies,
                headers=_bili_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.error("bilibili favorite request failed: %s", exc)
            return f"收藏失败（网络错误）: {exc}"
        return self._mutation_result("收藏", resp)

    def _resolve_default_fav_folder(
        self, cookies: dict[str, str]
    ) -> tuple[str | None, str | None]:
        try:
            nav_resp = requests.get(
                NAV_API, cookies=cookies, headers=_bili_headers(), timeout=REQUEST_TIMEOUT
            )
        except requests.RequestException as exc:
            log.error("bilibili nav request failed: %s", exc)
            return None, f"获取用户信息失败（网络错误）: {exc}"
        if nav_resp.status_code != 200:
            return None, f"获取用户信息 HTTP 错误: {nav_resp.status_code}"
        try:
            nav = nav_resp.json()
        except ValueError:
            return None, f"获取用户信息响应解析失败 (HTTP {nav_resp.status_code})"
        if nav.get("code") != 0:
            return None, self._api_error_text(nav)
        mid = (nav.get("data") or {}).get("mid")
        if not mid:
            return None, "无法获取用户 mid（cookie 可能无效）。"
        try:
            folder_resp = requests.get(
                FAV_FOLDER_API,
                params={"up_mid": mid},
                cookies=cookies,
                headers=_bili_headers(),
                timeout=REQUEST_TIMEOUT,
            )
        except requests.RequestException as exc:
            log.error("bilibili fav folder request failed: %s", exc)
            return None, f"获取收藏夹失败（网络错误）: {exc}"
        if folder_resp.status_code != 200:
            return None, f"获取收藏夹 HTTP 错误: {folder_resp.status_code}"
        try:
            folder_data = folder_resp.json()
        except ValueError:
            return None, f"获取收藏夹响应解析失败 (HTTP {folder_resp.status_code})"
        if folder_data.get("code") != 0:
            return None, self._api_error_text(folder_data)
        folders = (folder_data.get("data") or {}).get("list") or []
        if not folders:
            return None, "未找到收藏夹。"
        fid = folders[0].get("id")
        if not fid:
            return None, "收藏夹数据异常（缺少 id）。"
        return str(fid), None

    # ── 工具注册 ──

    @hookimpl
    def register_tools(self, ctx: PluginContext) -> list:
        plugin = self

        @tool
        def bilibili_search(keyword: str, page: int = 1) -> str:
            """
            Description:
                在 B 站搜索视频，返回前若干条结果的标题 / UP主 / bvid / 播放量（结果已截断，最多 800 字）。
            Args:
                keyword (str): 搜索关键词。
                page (int): 页码，默认 1。
            Returns:
                str: 搜索结果文本，或错误信息。
            """
            return plugin.search_videos(keyword, page)

        @tool
        def bilibili_video_info(bvid: str) -> str:
            """
            Description:
                获取 B 站视频的标题 / 简介 / UP主 / 分区 / 发布时间等信息。
            Args:
                bvid (str): 视频 BV 号，如 "BV1xx411c7mD"。
            Returns:
                str: 视频信息文本，或错误信息。
            """
            return plugin.video_info(bvid)

        @tool
        def bilibili_three_actions(
            bvid: str, like: bool = True, coin: bool = True, favorite: bool = True
        ) -> str:
            """
            Description:
                对 B 站视频执行点赞 / 投币 / 收藏（三连）。需要插件配置 SESSDATA cookie；未配置时返回明确错误。
            Args:
                bvid (str): 视频 BV 号。
                like (bool): 是否点赞，默认 True。
                coin (bool): 是否投一枚硬币，默认 True。
                favorite (bool): 是否收藏到默认收藏夹，默认 True。
            Returns:
                str: 各操作结果，或错误信息。
            """
            return plugin.three_actions(bvid, like, coin, favorite)

        return [
            ToolSpec(
                name="bilibili_search",
                tool=bilibili_search,
                enabled_by_default=True,
                description=bilibili_search.__doc__ or "",
            ),
            ToolSpec(
                name="bilibili_video_info",
                tool=bilibili_video_info,
                enabled_by_default=True,
                description=bilibili_video_info.__doc__ or "",
            ),
            ToolSpec(
                name="bilibili_three_actions",
                tool=bilibili_three_actions,
                enabled_by_default=True,
                description=bilibili_three_actions.__doc__ or "",
            ),
        ]


def get_plugin() -> Plugin:
    return Plugin()
