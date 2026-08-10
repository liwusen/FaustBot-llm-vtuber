from __future__ import annotations

import importlib.util
import sys
import time as _time
from pathlib import Path

import pytest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))

PLUGIN_DIR = Path(__file__).resolve().parents[1] / "default_plugins" / "bilibili-tools"


def _load(name: str):
    spec = importlib.util.spec_from_file_location(
        f"bilibili_tools_test_{name}", PLUGIN_DIR / f"{name}.py"
    )
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


impl = _load("impl")


class _FakeResp:
    def __init__(self, payload=None, status_code=200):
        self._payload = payload
        self.status_code = status_code

    def json(self):
        if self._payload is None:
            raise ValueError("no json payload")
        return self._payload


class _FakeCtx:
    plugin_data_dir = None
    plugin_dir = None

    def __init__(self, data_dir: Path, config: dict):
        self.plugin_data_dir = data_dir
        self.plugin_dir = data_dir
        self._config = dict(config)
        self._vfs = {}

    def register_config(self, schema):
        return None

    def get_config(self, key, default=None):
        return self._config.get(key, default)

    def vfs_write(self, path, content):
        self._vfs[path] = content


def _make_plugin(tmp_path, config: dict | None = None):
    p = impl.Plugin()
    ctx = _FakeCtx(tmp_path / "data", config or {"SESSDATA": "", "cookie_expired_hint": ""})
    p.startup(ctx)
    return p


@pytest.fixture()
def plugin(tmp_path):
    return _make_plugin(tmp_path)


# ── 工具注册 ──


def test_tools_registered(plugin):
    tools = plugin.register_tools(plugin.ctx)
    assert [t.name for t in tools] == [
        "bilibili_search",
        "bilibili_video_info",
        "bilibili_three_actions",
    ]
    assert all(t.enabled_by_default for t in tools)
    # langchain @tool 包装为 StructuredTool（可 .invoke）；langchain 不可用时回退为普通函数
    assert all(callable(t.tool) or hasattr(t.tool, "invoke") for t in tools)
    assert all((getattr(t.tool, "name", None) or t.tool.__name__) == t.name for t in tools)


# ── search：请求构造与响应解析 ──


def test_search_constructs_request_and_parses(monkeypatch, plugin):
    captured = {}

    def fake_get(url, params=None, headers=None, timeout=None):
        captured["url"] = url
        captured["params"] = params
        return _FakeResp(
            {
                "code": 0,
                "message": "0",
                "data": {
                    "result": [
                        {
                            "type": "video",
                            "title": '<em class="keyword">测试</em>视频',
                            "author": "某UP主",
                            "bvid": "BV1xx411c7mD",
                            "play": 123456,
                        }
                    ]
                },
            }
        )

    monkeypatch.setattr(impl.requests, "get", fake_get)
    out = plugin.search_videos("测试 视频", page=2)

    assert captured["url"] == impl.SEARCH_API
    assert captured["params"] == {"search_type": "video", "keyword": "测试 视频", "page": 2}
    # 标题中的 <em class="keyword"> 标签被剥离
    assert "测试视频" in out
    assert "某UP主" in out
    assert "BV1xx411c7mD" in out
    assert "123456" in out
    # 搜索无需 cookie
    assert "SESSDATA" not in out
    # 结果缓存到 VFS
    assert any(p.startswith("/plugins/bilibili_tools/search-") for p in plugin.ctx._vfs)


def test_search_empty_keyword_no_network(monkeypatch, plugin):
    def boom(*args, **kwargs):
        raise AssertionError("should not hit network")

    monkeypatch.setattr(impl.requests, "get", boom)
    assert plugin.search_videos("   ") == "搜索关键词为空。"


def test_search_http_error_contains_status(monkeypatch, plugin):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp(None, status_code=503)

    monkeypatch.setattr(impl.requests, "get", fake_get)
    out = plugin.search_videos("kw")
    assert "503" in out


def test_search_api_error_code_surfaced(monkeypatch, plugin):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp({"code": -412, "message": "请求被拦截"})

    monkeypatch.setattr(impl.requests, "get", fake_get)
    out = plugin.search_videos("kw")
    assert "失败" in out
    assert "-412" in out


def test_search_no_results(monkeypatch, plugin):
    def fake_get(url, params=None, headers=None, timeout=None):
        return _FakeResp({"code": 0, "data": {"result": []}})

    monkeypatch.setattr(impl.requests, "get", fake_get)
    assert "未找到相关视频" in plugin.search_videos("不存在的关键词")


# ── search：结果截断 ──


def test_search_result_truncated(monkeypatch, plugin):
    long_title = "很" * 200

    def fake_get(url, params=None, headers=None, timeout=None):
        payload = {
            "code": 0,
            "data": {
                "result": [
                    {
                        "type": "video",
                        "title": long_title,
                        "author": "a",
                        "bvid": f"BV{i}",
                        "play": 1,
                    }
                    for i in range(10)
                ]
            },
        }
        return _FakeResp(payload)

    monkeypatch.setattr(impl.requests, "get", fake_get)
    out = plugin.search_videos("keyword")
    assert len(out) <= impl.MAX_RESULT_CHARS


# ── video_info ──


def test_video_info_parses(monkeypatch, plugin):
    pubdate = 1700000000
    expected_pub = _time.strftime("%Y-%m-%d", _time.localtime(pubdate))

    def fake_get(url, params=None, headers=None, timeout=None):
        assert params == {"bvid": "BV1xx411c7mD"}
        return _FakeResp(
            {
                "code": 0,
                "data": {
                    "title": "测试视频标题",
                    "desc": "简介内容",
                    "owner": {"name": "某UP主"},
                    "tname": "科技",
                    "pubdate": pubdate,
                    "stat": {"view": 100, "like": 10, "danmaku": 5},
                },
            }
        )

    monkeypatch.setattr(impl.requests, "get", fake_get)
    out = plugin.video_info("BV1xx411c7mD")
    assert "测试视频标题" in out
    assert "某UP主" in out
    assert "科技" in out
    assert expected_pub in out
    assert "简介内容" in out
    assert "BV1xx411c7mD" in out


def test_video_info_missing_bvid(monkeypatch, plugin):
    def boom(*args, **kwargs):
        raise AssertionError("should not hit network")

    monkeypatch.setattr(impl.requests, "get", boom)
    assert plugin.video_info("") == "缺少 bvid 参数。"


# ── three_actions：无 cookie ──


def test_three_actions_without_cookie(monkeypatch, plugin):
    def boom(*args, **kwargs):
        raise AssertionError("should not hit network")

    monkeypatch.setattr(impl.requests, "get", boom)
    monkeypatch.setattr(impl.requests, "post", boom)
    out = plugin.three_actions("BV1xx411c7mD")
    assert "SESSDATA" in out
    assert "不可用" in out
    assert "搜索与视频信息功能仍可使用" in out


# ── three_actions：有 cookie 时的请求构造 ──


def _plugin_with_cookie(tmp_path, raw_cookie):
    return _make_plugin(tmp_path, {"SESSDATA": raw_cookie, "cookie_expired_hint": ""})


def test_three_actions_posts_with_cookie(monkeypatch, tmp_path):
    plugin = _plugin_with_cookie(tmp_path, "SESSDATA=abc; bili_jct=xyz")
    posts = []

    def fake_post(url, data=None, cookies=None, headers=None, timeout=None):
        posts.append((url, dict(data or {}), dict(cookies or {})))
        return _FakeResp({"code": 0, "message": "0"})

    def fake_get(url, params=None, cookies=None, headers=None, timeout=None):
        if url == impl.NAV_API:
            return _FakeResp({"code": 0, "data": {"mid": 12345}})
        if url == impl.FAV_FOLDER_API:
            return _FakeResp({"code": 0, "data": {"list": [{"id": 999}]}})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(impl.requests, "post", fake_post)
    monkeypatch.setattr(impl.requests, "get", fake_get)

    out = plugin.three_actions("BV1xx411c7mD")
    assert "点赞成功" in out
    assert "投币成功" in out
    assert "收藏成功" in out

    urls = [p[0] for p in posts]
    assert impl.LIKE_API in urls
    assert impl.COIN_API in urls
    assert impl.FAV_DEAL_API in urls

    like_post = next(p for p in posts if p[0] == impl.LIKE_API)
    assert like_post[1] == {"bvid": "BV1xx411c7mD", "like": 1, "csrf": "xyz"}
    assert like_post[2] == {"SESSDATA": "abc", "bili_jct": "xyz"}

    coin_post = next(p for p in posts if p[0] == impl.COIN_API)
    assert coin_post[1]["bvid"] == "BV1xx411c7mD"
    assert coin_post[1]["multiply"] == 1
    assert coin_post[1]["select_like"] == 1  # 点赞时顺带点亮

    fav_post = next(p for p in posts if p[0] == impl.FAV_DEAL_API)
    assert fav_post[1]["rid"] == "BV1xx411c7mD"
    assert fav_post[1]["type"] == 2
    assert fav_post[1]["add_media_ids"] == "999"
    assert fav_post[1]["csrf"] == "xyz"


def test_three_actions_partial_flags(monkeypatch, tmp_path):
    plugin = _plugin_with_cookie(tmp_path, "SESSDATA=abc; bili_jct=xyz")
    posts = []

    def fake_post(url, data=None, cookies=None, headers=None, timeout=None):
        posts.append(url)
        return _FakeResp({"code": 0})

    def fake_get(url, params=None, cookies=None, headers=None, timeout=None):
        if url == impl.NAV_API:
            return _FakeResp({"code": 0, "data": {"mid": 1}})
        if url == impl.FAV_FOLDER_API:
            return _FakeResp({"code": 0, "data": {"list": [{"id": 1}]}})
        raise AssertionError(f"unexpected GET {url}")

    monkeypatch.setattr(impl.requests, "post", fake_post)
    monkeypatch.setattr(impl.requests, "get", fake_get)

    out = plugin.three_actions("BV1xx411c7mD", like=False, coin=False, favorite=True)
    assert posts == [impl.FAV_DEAL_API]
    assert "收藏成功" in out


def test_three_actions_mutation_error_contains_code(monkeypatch, tmp_path):
    plugin = _plugin_with_cookie(tmp_path, "SESSDATA=abc; bili_jct=xyz")

    def fake_post(url, data=None, cookies=None, headers=None, timeout=None):
        return _FakeResp({"code": -111, "message": "csrf 校验失败"})

    monkeypatch.setattr(impl.requests, "post", fake_post)
    monkeypatch.setattr(impl.requests, "get", lambda *a, **k: _FakeResp({"code": 0, "data": {}}))

    out = plugin.three_actions("BV1xx411c7mD", favorite=False)
    assert "点赞失败" in out
    assert "-111" in out
    assert "cookie" in out  # 附带 cookie 过期提示


def test_three_actions_missing_bili_jct_hint(monkeypatch, tmp_path):
    # 仅配置 SESSDATA（无 bili_jct）时，三连应返回明确的 csrf 提示而非 -111
    plugin = _plugin_with_cookie(tmp_path, "SESSDATA=abc")

    def fake_post(url, data=None, cookies=None, headers=None, timeout=None):
        raise AssertionError("不应发起任何请求")

    monkeypatch.setattr(impl.requests, "post", fake_post)
    out = plugin.three_actions("BV1xx411c7mD")
    assert "bili_jct" in out
    assert "三连失败" in out


# ── cookie 解析 ──


def test_parse_cookie_config_bare_value():
    cookies, csrf = impl._parse_cookie_config("abc123")
    assert cookies == {"SESSDATA": "abc123"}
    assert csrf == ""


def test_parse_cookie_config_bare_value_with_equals():
    # SESSDATA 值本身是 base64，常含 '='（如 abc=def），不得误判为完整 cookie 串
    cookies, csrf = impl._parse_cookie_config("abc=def")
    assert cookies == {"SESSDATA": "abc=def"}
    assert csrf == ""


def test_parse_cookie_config_full_string():
    cookies, csrf = impl._parse_cookie_config("SESSDATA=abc; bili_jct=xyz; other=1")
    assert cookies == {"SESSDATA": "abc", "bili_jct": "xyz", "other": "1"}
    assert csrf == "xyz"


def test_parse_cookie_config_full_string_sessdata_value_with_equals():
    # 完整 cookie 串中 SESSDATA 值含 '=' 时按第一个 '=' 切分
    cookies, csrf = impl._parse_cookie_config("SESSDATA=abc=def; bili_jct=xyz")
    assert cookies == {"SESSDATA": "abc=def", "bili_jct": "xyz"}
    assert csrf == "xyz"


def test_parse_cookie_config_empty():
    assert impl._parse_cookie_config("") == ({}, "")
    assert impl._parse_cookie_config("   ") == ({}, "")
