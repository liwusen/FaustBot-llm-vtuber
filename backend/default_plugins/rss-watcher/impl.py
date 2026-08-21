from __future__ import annotations

import asyncio
import json
import re
import sqlite3
import threading
import time
import xml.etree.ElementTree as ET
from pathlib import Path
from typing import Any

import aiohttp

from faust_backend.plugin_system import FaustPlugin, PluginContext, hookimpl
from faust_backend.logger import get_logger

log = get_logger("faust.plugin.rss-watcher")
_PLUGIN: "Plugin | None" = None
DB_NAME = "data.db"
SCHEMA_SQL = """
CREATE TABLE IF NOT EXISTS feeds (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    url TEXT NOT NULL,
    name TEXT NOT NULL,
    category TEXT DEFAULT '',
    last_fetch INTEGER DEFAULT 0,
    error_count INTEGER DEFAULT 0
);
CREATE TABLE IF NOT EXISTS items (
    id INTEGER PRIMARY KEY AUTOINCREMENT,
    feed_id INTEGER NOT NULL,
    title TEXT NOT NULL,
    link TEXT DEFAULT '',
    summary TEXT DEFAULT '',
    published INTEGER DEFAULT 0,
    is_read INTEGER DEFAULT 0,
    is_saved INTEGER DEFAULT 0,
    is_pushed INTEGER DEFAULT 0,
    UNIQUE(feed_id, link)
);
"""


def _sanitize_title(value: str) -> str:
    cleaned = re.sub(r"[\\/;:]+", "", str(value or "")).strip()
    return cleaned or "untitled"


def _now() -> int:
    return int(time.time())


def _run_async_background(coro) -> None:
    def runner() -> None:
        loop = asyncio.new_event_loop()
        try:
            asyncio.set_event_loop(loop)
            loop.run_until_complete(coro)
        finally:
            try:
                loop.close()
            except Exception:
                pass

    threading.Thread(target=runner, daemon=True).start()



def _parse_time_to_minutes(raw: str) -> int:
    try:
        hour, minute = str(raw or "0:0").split(":", 1)
        return max(0, min(23, int(hour))) * 60 + max(0, min(59, int(minute)))
    except Exception:
        return 0


class RSSWatcherStore:
    def __init__(self, data_dir: Path):
        self._lock = threading.RLock()
        self._data_dir = data_dir
        self._data_dir.mkdir(parents=True, exist_ok=True)
        self._db_path = self._data_dir / DB_NAME
        self._meta_path = self._data_dir / "runtime.json"
        self._ensure_db()

    def _connect(self) -> sqlite3.Connection:
        conn = sqlite3.connect(self._db_path)
        conn.row_factory = sqlite3.Row
        return conn

    def _ensure_db(self) -> None:
        with self._connect() as conn:
            conn.executescript(SCHEMA_SQL)
            conn.commit()
        if not self._meta_path.exists():
            self.save_meta(
                {"last_fetch_ts": 0, "last_banner_item_id": 0, "last_digest_ts": 0}
            )

    def load_meta(self) -> dict[str, Any]:
        try:
            return json.loads(self._meta_path.read_text(encoding="utf-8"))
        except Exception:
            return {"last_fetch_ts": 0, "last_banner_item_id": 0, "last_digest_ts": 0}

    def save_meta(self, meta: dict[str, Any]) -> None:
        self._meta_path.write_text(
            json.dumps(meta, ensure_ascii=False, indent=2), encoding="utf-8"
        )

    def list_feeds(self) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT id, url, name, category, last_fetch, error_count FROM feeds ORDER BY id DESC"
            ).fetchall()
            return [dict(row) for row in rows]

    def add_feed(self, url: str, name: str, category: str) -> dict[str, Any]:
        with self._lock, self._connect() as conn:
            cursor = conn.execute(
                "INSERT INTO feeds(url, name, category, last_fetch, error_count) VALUES (?, ?, ?, 0, 0)",
                (url, name, category),
            )
            conn.commit()
            row = conn.execute(
                "SELECT id, url, name, category, last_fetch, error_count FROM feeds WHERE id = ?",
                (cursor.lastrowid,),
            ).fetchone()
            return (
                dict(row)
                if row
                else {
                    "id": int(cursor.lastrowid or 0),
                    "url": url,
                    "name": name,
                    "category": category,
                }
            )

    def update_feed(
        self, feed_id: int, *, url: str, name: str, category: str
    ) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE feeds SET url = ?, name = ?, category = ? WHERE id = ?",
                (url, name, category, feed_id),
            )
            row = conn.execute(
                "SELECT id, url, name, category, last_fetch, error_count FROM feeds WHERE id = ?",
                (feed_id,),
            ).fetchone()
            conn.commit()
            return dict(row) if row else None

    def delete_feed(self, feed_id: int) -> bool:
        with self._lock, self._connect() as conn:
            conn.execute("DELETE FROM items WHERE feed_id = ?", (feed_id,))
            cursor = conn.execute("DELETE FROM feeds WHERE id = ?", (feed_id,))
            conn.commit()
            return cursor.rowcount > 0

    def list_items(self, limit: int = 50, offset: int = 0) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT i.id, i.feed_id, i.title, i.link, i.summary, i.published, i.is_read, i.is_saved, i.is_pushed, f.name AS feed_name, f.category AS feed_category FROM items i LEFT JOIN feeds f ON i.feed_id = f.id ORDER BY i.published DESC, i.id DESC LIMIT ? OFFSET ?",
                (limit, offset),
            ).fetchall()
            return [dict(row) for row in rows]

    def insert_items(
        self, feed_id: int, items: list[dict[str, Any]], max_items: int
    ) -> int:
        inserted = 0
        with self._lock, self._connect() as conn:
            for item in items:
                link = str(item.get("link") or "").strip()
                title = str(item.get("title") or "未命名条目").strip()
                summary = str(item.get("summary") or "").strip()
                published = int(item.get("published") or _now())
                try:
                    conn.execute(
                        "INSERT OR IGNORE INTO items(feed_id, title, link, summary, published, is_read, is_saved, is_pushed) VALUES (?, ?, ?, ?, ?, 0, 0, 0)",
                        (feed_id, title, link, summary, published),
                    )
                    if conn.total_changes > 0:
                        inserted += 1
                except sqlite3.IntegrityError:
                    continue
            conn.execute(
                "DELETE FROM items WHERE id NOT IN (SELECT id FROM items ORDER BY published DESC, id DESC LIMIT ?)",
                (max_items,),
            )
            conn.execute(
                "UPDATE feeds SET last_fetch = ?, error_count = 0 WHERE id = ?",
                (_now(), feed_id),
            )
            conn.commit()
        return inserted

    def mark_fetch_error(self, feed_id: int) -> None:
        with self._lock, self._connect() as conn:
            conn.execute(
                "UPDATE feeds SET error_count = error_count + 1, last_fetch = ? WHERE id = ?",
                (_now(), feed_id),
            )
            conn.commit()

    def count_unpushed_items(self, category: str | None = None) -> int:
        with self._lock, self._connect() as conn:
            if category and category != "all":
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM items i JOIN feeds f ON i.feed_id = f.id WHERE i.is_pushed = 0 AND f.category = ?",
                    (category,),
                ).fetchone()
            else:
                row = conn.execute(
                    "SELECT COUNT(*) AS c FROM items WHERE is_pushed = 0"
                ).fetchone()
            return int((row or {"c": 0})["c"])

    def newest_unpushed(
        self, limit: int, category: str | None = None
    ) -> list[dict[str, Any]]:
        with self._lock, self._connect() as conn:
            if category and category != "all":
                rows = conn.execute(
                    "SELECT i.id, i.title, i.link, i.summary, i.published, f.name AS feed_name FROM items i JOIN feeds f ON i.feed_id = f.id WHERE i.is_pushed = 0 AND f.category = ? ORDER BY i.published DESC, i.id DESC LIMIT ?",
                    (category, limit),
                ).fetchall()
            else:
                rows = conn.execute(
                    "SELECT i.id, i.title, i.link, i.summary, i.published, f.name AS feed_name FROM items i LEFT JOIN feeds f ON i.feed_id = f.id WHERE i.is_pushed = 0 ORDER BY i.published DESC, i.id DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [dict(row) for row in rows]

    def mark_pushed(self, item_ids: list[int]) -> None:
        if not item_ids:
            return
        placeholders = ",".join("?" for _ in item_ids)
        with self._lock, self._connect() as conn:
            conn.execute(
                f"UPDATE items SET is_pushed = 1 WHERE id IN ({placeholders})", item_ids
            )
            conn.commit()

    def mark_saved(self, item_id: int) -> dict[str, Any] | None:
        with self._lock, self._connect() as conn:
            conn.execute("UPDATE items SET is_saved = 1 WHERE id = ?", (item_id,))
            row = conn.execute(
                "SELECT i.id, i.title, i.link, i.summary, i.published, f.name AS feed_name FROM items i LEFT JOIN feeds f ON i.feed_id = f.id WHERE i.id = ?",
                (item_id,),
            ).fetchone()
            conn.commit()
            return dict(row) if row else None

    def saved_items_for_last_day(self) -> list[dict[str, Any]]:
        cutoff = _now() - 86400
        with self._lock, self._connect() as conn:
            rows = conn.execute(
                "SELECT i.id, i.title, i.link, i.summary, i.published, f.name AS feed_name FROM items i LEFT JOIN feeds f ON i.feed_id = f.id WHERE i.published >= ? ORDER BY i.published DESC, i.id DESC",
                (cutoff,),
            ).fetchall()
            return [dict(row) for row in rows]

    def build_digest(
        self, limit: int = 5, category: str | None = None
    ) -> dict[str, Any]:
        items = self.newest_unpushed(limit=limit, category=category)
        if not items:
            items = self.list_items(limit=limit, offset=0)
        lines = []
        for item in items:
            source = item.get("feed_name") or "RSS"
            title = item.get("title") or "未命名条目"
            summary = (item.get("summary") or "").strip()
            line = f"- [{source}] {title}"
            if summary:
                line += f"：{summary[:120]}"
            lines.append(line)
        return {
            "count": len(items),
            "items": items,
            "summary": "\n".join(lines) if lines else "今天还没有可播报的新条目。",
        }

    def get_banner_item(self) -> dict[str, Any] | None:
        meta = self.load_meta()
        last_banner_item_id = int(meta.get("last_banner_item_id") or 0)
        with self._lock, self._connect() as conn:
            row = conn.execute(
                "SELECT i.id, i.title, i.link, i.summary, f.name AS feed_name FROM items i LEFT JOIN feeds f ON i.feed_id = f.id WHERE i.id > ? ORDER BY i.id ASC LIMIT 1",
                (last_banner_item_id,),
            ).fetchone()
            if row is None:
                return None
            meta["last_banner_item_id"] = int(row["id"])
            self.save_meta(meta)
            return dict(row)


async def _fetch_text(url: str) -> str:
    timeout = aiohttp.ClientTimeout(total=20)
    async with aiohttp.ClientSession(timeout=timeout) as session:
        async with session.get(
            url, headers={"User-Agent": "FaustBot RSS Watcher/0.1"}
        ) as response:
            response.raise_for_status()
            return await response.text()


def _strip_ns(tag: str) -> str:
    return tag.split("}", 1)[-1]


def _parse_feed(xml_text: str) -> list[dict[str, Any]]:
    root = ET.fromstring(xml_text)
    items: list[dict[str, Any]] = []
    if _strip_ns(root.tag) == "rss":
        channel = root.find("channel")
        if channel is not None:
            for item in channel.findall("item"):
                title = (item.findtext("title") or "").strip()
                link = (item.findtext("link") or "").strip()
                summary = (item.findtext("description") or "").strip()
                published = _now()
                items.append(
                    {
                        "title": title,
                        "link": link,
                        "summary": summary,
                        "published": published,
                    }
                )
    else:
        for entry in root.findall(".//{*}entry"):
            title = (entry.findtext("{*}title") or "").strip()
            link = ""
            link_node = entry.find("{*}link")
            if link_node is not None:
                link = str(link_node.attrib.get("href") or "").strip()
            summary = (
                entry.findtext("{*}summary") or entry.findtext("{*}content") or ""
            ).strip()
            items.append(
                {"title": title, "link": link, "summary": summary, "published": _now()}
            )
    return [item for item in items if item.get("title") or item.get("link")]


class Plugin(FaustPlugin):
    def __init__(self):
        self.ctx: PluginContext
        self.store: RSSWatcherStore | None = None
        self.last_user_activity_ts = time.time()
        self._configs_cache: dict[str, Any] = {}

    def _get_config_sync(self, key: str, default: Any = None) -> Any:
        return self._configs_cache.get(key, default)

    async def startup(self, ctx: PluginContext) -> None:
        self.ctx = ctx
        data_dir = ctx.plugin_data_dir or (ctx.plugin_dir / "data")
        self.store = RSSWatcherStore(data_dir)
        await ctx.register_config(
            [
                {
                    "key": "PUSH_THRESHOLD",
                    "type": "int",
                    "label": "推送阈值（条）",
                    "default": 3,
                },
                {
                    "key": "FETCH_INTERVAL_MIN",
                    "type": "int",
                    "label": "拉取间隔（分钟）",
                    "default": 15,
                },
                {
                    "key": "QUIET_START",
                    "type": "str",
                    "label": "静默开始",
                    "default": "23:00",
                },
                {
                    "key": "QUIET_END",
                    "type": "str",
                    "label": "静默结束",
                    "default": "08:00",
                },
                {
                    "key": "CATEGORY_FILTER",
                    "type": "str",
                    "label": "分类过滤",
                    "default": "all",
                },
                {
                    "key": "MAX_ITEMS",
                    "type": "int",
                    "label": "最多保留条目",
                    "default": 500,
                },
            ]
        )
        self._configs_cache = await ctx.list_configs()
        await ctx.vfs_write(
            "/plugins/rss-watcher.md",
            "# RSS Watcher\n\n"
            "RSS Watcher 会把 RSS 正文存到 faustbot://plugins/rss-watcher/ 下。\n"
            "正常对话和被 event-trigger 唤醒时，都可以优先读取 faustbot://plugins/rss-watcher/index.md 获取最近一天的更新概览。\n"
            "若要看某条 RSS 的正文，请读取 faustbot://plugins/rss-watcher/RSS-FEED-*.md。\n",
        )
        await ctx.vfs_write(
            "/plugins/rss-watcher/index.md", "# RSS Watcher\n\n暂无 RSS 更新。"
        )

    @hookimpl
    def plugin_loaded(self, ctx: PluginContext) -> None:
        print("LOADED RSS Watcher plugin")
        global _PLUGIN
        _PLUGIN = self

    @hookimpl
    def plugin_unloaded(self, ctx: PluginContext) -> None:
        print("UNLOADED RSS Watcher plugin")
        global _PLUGIN
        if _PLUGIN is self:
            _PLUGIN = None

    @hookimpl
    async def config_changed(self, key: str, old: Any, new: Any, ctx: PluginContext) -> None:
        # 用 startup 保存的 self.ctx（hook 参数 ctx 可能为 None）
        if self.ctx is not None:
            self._configs_cache = await self.ctx.list_configs()

    def category_filter(self) -> str:
        if self.ctx is None:
            return "all"
        return str(self._get_config_sync("CATEGORY_FILTER", "all") or "all")

    def register_frontend(self) -> list[dict]:
        return [
            {"type": "js", "path": "/faust/plugins/rss-watcher/frontend/panel-v2.js"},
            {
                "type": "js",
                "path": "/faust/plugins/rss-watcher/frontend/app-hook-v2.js",
            },
            {"type": "css", "path": "/faust/plugins/rss-watcher/frontend/panel-v2.css"},
        ]

    def register_schedules(self) -> list[dict]:
        def scheduled_fetch() -> None:
            if self.ctx is None or self.store is None:
                return
            meta = self.store.load_meta()
            interval_min = int(self._get_config_sync("FETCH_INTERVAL_MIN", 15) or 15)
            if _now() - int(meta.get("last_fetch_ts") or 0) < interval_min * 60:
                return
            _run_async_background(self.fetch_all_feeds())

        return [
            {
                "id": "rss-watcher-fetch",
                "interval": 30,
                "callback": scheduled_fetch,
                "description": "抓取 RSS 更新",
            }
        ]

    async def fetch_all_feeds(self) -> dict[str, Any]:
        if self.store is None or self.ctx is None:
            return {"fetched": 0, "inserted": 0, "errors": []}
        fetched = 0
        inserted = 0
        errors: list[dict[str, Any]] = []
        max_items = int(self._get_config_sync("MAX_ITEMS", 500) or 500)
        feeds = self.store.list_feeds()
        for feed in feeds:
            try:
                xml_text = await _fetch_text(str(feed.get("url") or ""))
                items = _parse_feed(xml_text)
                inserted += self.store.insert_items(
                    int(feed["id"]), items, max_items=max_items
                )
                for item in items:
                    await self._write_item_to_vfs(item, str(feed.get("name") or "RSS"))
                fetched += 1
            except Exception as exc:
                self.store.mark_fetch_error(int(feed["id"]))
                errors.append({"feed_id": feed.get("id"), "error": str(exc)})
        meta = self.store.load_meta()
        meta["last_fetch_ts"] = _now()
        self.store.save_meta(meta)
        await self._write_daily_index()
        return {"fetched": fetched, "inserted": inserted, "errors": errors}

    async def communicate_handler(self, payload: dict, ctx: PluginContext) -> dict | None:
        action = str((payload or {}).get("action") or "").strip().lower()
        if self.store is None:
            return {"status": "error", "detail": "plugin not loaded"}
        if action == "get_feeds":
            return {"status": "ok", "items": self.store.list_feeds()}
        if action == "create_feed":
            url = str((payload or {}).get("url") or "").strip()
            name = str((payload or {}).get("name") or url).strip()
            category = str((payload or {}).get("category") or "").strip()
            if not url:
                return {"status": "error", "detail": "缺少 RSS URL"}
            return {"status": "ok", "item": self.store.add_feed(url=url, name=name, category=category)}
        if action == "delete_feed":
            feed_id = int((payload or {}).get("feed_id") or 0)
            return {"status": "ok", "deleted": self.store.delete_feed(feed_id)}
        if action == "update_feed":
            feed_id = int((payload or {}).get("feed_id") or 0)
            url = str((payload or {}).get("url") or "").strip()
            name = str((payload or {}).get("name") or url).strip()
            category = str((payload or {}).get("category") or "").strip()
            if not url:
                return {"status": "error", "detail": "缺少 RSS URL"}
            item = self.store.update_feed(feed_id, url=url, name=name, category=category)
            if item is None:
                return {"status": "error", "detail": "订阅源不存在"}
            return {"status": "ok", "item": item}
        if action == "get_items":
            limit = int((payload or {}).get("limit") or 50)
            offset = int((payload or {}).get("offset") or 0)
            return {"status": "ok", "items": self.store.list_items(limit=limit, offset=offset)}
        if action == "get_digest":
            return {"status": "ok", **self.store.build_digest(limit=5, category=self.category_filter())}
        if action == "get_banner":
            return {"status": "ok", "item": self.store.get_banner_item()}
        if action == "save_item":
            item_id = int((payload or {}).get("item_id") or 0)
            item = self.store.mark_saved(item_id)
            if item is None:
                return {"status": "error", "detail": "条目不存在"}
            self.write_saved_item_to_memory(item)
            return {"status": "ok", "item": item}
        if action == "fetch_now":
            return {"status": "ok", **(await self.fetch_all_feeds())}
        return {"status": "error", "detail": f"unknown action: {action}"}

    async def _write_item_to_vfs(self, item: dict[str, Any], feed_name: str) -> None:
        if self.ctx is None:
            return
        title = _sanitize_title(item.get("title") or "untitled")
        stamp = time.strftime(
            "%Y%m%d", time.localtime(int(item.get("published") or _now()))
        )
        path = f"/plugins/rss-watcher/RSS-FEED-{title}-{stamp}.md"
        content = (
            f"# {item.get('title') or title}\n\n"
            f"- 来源: {feed_name}\n"
            f"- 链接: {item.get('link') or ''}\n"
            f"- 时间: {stamp}\n\n"
            f"{item.get('summary') or ''}\n"
        )
        await self.ctx.vfs_write(path, content)

    async def _write_daily_index(self) -> None:
        if self.ctx is None or self.store is None:
            return
        items = self.store.saved_items_for_last_day()
        lines = ["# RSS Watcher Index", "", "最近一天 RSS 更新："]
        if not items:
            lines.append("- 暂无更新")
        for item in items:
            title = _sanitize_title(item.get("title") or "untitled")
            stamp = time.strftime(
                "%Y%m%d", time.localtime(int(item.get("published") or _now()))
            )
            lines.append(
                f"- {item.get('feed_name') or 'RSS'} | {item.get('title') or title} | faustbot://plugins/rss-watcher/RSS-FEED-{title}-{stamp}.md"
            )
        await self.ctx.vfs_write("/plugins/rss-watcher/index.md", "\n".join(lines) + "\n")

    def _in_quiet_hours(self) -> bool:
        if self.ctx is None:
            return False
        now_minutes = time.localtime().tm_hour * 60 + time.localtime().tm_min
        start = _parse_time_to_minutes(
            str(self._get_config_sync("QUIET_START", "23:00") or "23:00")
        )
        end = _parse_time_to_minutes(
            str(self._get_config_sync("QUIET_END", "08:00") or "08:00")
        )
        if start == end:
            return False
        if start < end:
            return start <= now_minutes < end
        return now_minutes >= start or now_minutes < end

    def _idle_seconds(self) -> int:
        return int(max(0, time.time() - self.last_user_activity_ts))

    async def _queue_digest_trigger(self, digest: dict[str, Any]) -> None:
        if self.store is None:
            return
        items = digest.get("items") or []
        ids = [int(item["id"]) for item in items if item.get("id")]
        if ids:
            self.store.mark_pushed(ids)
        try:
            payload = {
                "id": f"rss_digest::{_now()}",
                "type": "event",
                "event_name": "rss_digest",
                "payload": {"summary": digest.get("summary", ""), "items": items},
                "recall_description": "RSS Watcher 检测到一批新条目，可主动播报摘要。",
                "lifespan": 7200,
            }
            log.debug("Triggering RSS digest event: %s", payload)
            await self.ctx.trigger_create(payload)
        except Exception:
            pass
        meta = self.store.load_meta()
        meta["last_digest_ts"] = _now()
        self.store.save_meta(meta)

    def write_saved_item_to_memory(self, item: dict[str, Any]) -> None:
        async def writer() -> None:
            from faust_backend.memory import get_memory

            content = (
                f"# RSS 收藏\n\n"
                f"- 标题: {item.get('title') or ''}\n"
                f"- 来源: {item.get('feed_name') or ''}\n"
                f"- 链接: {item.get('link') or ''}\n"
                f"- 摘要: {item.get('summary') or ''}\n"
            )
            await get_memory().file_write(
                "/rss/saved/" + str(item.get("id")) + ".md",
                content,
                description="RSS saved item",
                declared_by="rss-watcher",
                index=True,
                tags=["rss", "saved"],
            )

        _run_async_background(writer())

    @hookimpl
    def message_received(
        self, msg: Any, history: list, ctx: PluginContext
    ) -> str | None:
        self.last_user_activity_ts = time.time()
        return None

    @hookimpl
    def memory_write_post(
        self, content: str, metadata: dict | None, id: str, ctx: PluginContext
    ) -> None:
        if self.store is None:
            return None
        text = str(content or "")
        if "RSS_SAVED:" not in text:
            return None
        try:
            item_id = int(text.split("RSS_SAVED:", 1)[1].split()[0])
        except Exception:
            return None
        item = self.store.mark_saved(item_id)
        if item:
            self.write_saved_item_to_memory(item)
        return None

    async def heartbeat(self, ctx: PluginContext) -> None:
        log.debug("RSS Watcher heartbeat check")
        if self.store is None or self.ctx is None:
            return
        threshold = int(self._get_config_sync("PUSH_THRESHOLD", 3) or 3)
        pending = self.store.count_unpushed_items(category=self.category_filter())
        log.debug("Pending unpushed items: %d, threshold: %d", pending, threshold)
        log.debug("Idle seconds: %d", self._idle_seconds())
        log.debug("In quiet hours: %s", self._in_quiet_hours())
        if pending < threshold:
            return
        if self._idle_seconds() < 120:
            return
        if self._in_quiet_hours():
            return
        digest = self.store.build_digest(
            limit=threshold, category=self.category_filter()
        )
        log.debug("Digest built: %s", digest)
        if digest.get("count", 0) <= 0:
            return
        await self._write_daily_index()
        await self._queue_digest_trigger(digest)

    def register_prompt_suffix(self) -> list[str]:
        return [
            "\n[RSS Watcher]\n"
            "RSS Watcher 会把最近一天的索引写到 faustbot://plugins/rss-watcher/index.md。"
            "无论是正常对话还是被 RSS 的 event-trigger 唤醒，都应该先读取这个索引，再按需读取对应的 RSS-FEED 文档。\n"
        ]

    def health_check(self) -> dict | None:
        return {
            "status": "ok",
            "plugin": "rss-watcher",
            "feeds": len(self.store.list_feeds()) if self.store else 0,
        }


def get_plugin() -> Plugin:
    return Plugin()
