"""models.dev 目录：下载、缓存、Provider 匹配与「通用」模型路由推断。

数据来源：https://models.dev/api.json（上游仓库 MIT license）。

本模块**不含任何具体 Provider 的模型→格式表**。它只做四件事：

1. 把 `api.json` 拉到本地缓存（`~/.faustbot/cache/models_dev/api.json`，TTL 24h），
   超时/5xx/超大/非 JSON 一律保留旧缓存并记录错误，绝不阻塞启动；
2. 用「host + 模型名重合度」把用户配置的 Provider 匹配到目录条目；
3. 把目录里每个模型条目的 `provider.npm` / `provider.shape` / `provider.api`
   按通用协议规则翻译成四种 API 格式之一（见 `llm_formats.detect_api_format`），
   并把 `reasoning_options` 规范成思考能力（effort / budget_tokens / toggle）；
4. 把结果写回 `ModelProvider.model_catalog_routes` 等字段，供构建模型时离线读取。

`host_matched=False` 的语义：只按模型名重合匹配到目录，但 base_url 主机不同。
此时**只采用目录推断出的格式**，端点仍用用户自己的 `base_url`——绝不能把请求
发到用户没有配置（也没有 key）的主机上。
"""

from __future__ import annotations

import asyncio
import json
import os
import time
from dataclasses import dataclass
from typing import Any
from urllib.parse import urlparse

import faust_backend.core.config as conf
from faust_backend.llm_formats import API_FORMATS, detect_api_format, normalize_format_base_url
from faust_backend.core.log import get_logger
from faust_backend.thinking_caps import capability_from_catalog_model

log = get_logger("faust.models_dev")

MODELS_DEV_URL = "https://models.dev/api.json?type=all"
CACHE_TTL_SECONDS = 24 * 3600
# api.json 全量约 6~10MB；超过该上限说明响应异常，直接失败而不是把内存吃满。
MAX_BYTES = 32 * 1024 * 1024
# 仅按模型重合匹配时的最低覆盖率（避免把一堆不相干 Provider 匹配上）
MIN_OVERLAP = 0.8
# 仅按模型重合匹配时，至少要解释这么多个模型（单个模型碰巧同名不算证据）
MIN_OVERLAP_MODELS = 2

# ── 内存态 ──
_catalog: dict[str, Any] | None = None
_catalog_loaded_at: float = 0.0  # time.time()；0 表示内存里没有目录
_catalog_source: str = ""  # "network" | "cache" | ""
_last_error: str = ""
_refresh_lock = asyncio.Lock()


# ── 路径与缓存 ──


def cache_path() -> str:
    """目录缓存文件路径（`~/.faustbot/cache/models_dev/api.json`）。"""
    return os.path.join(conf.CONFIG_ROOT, "cache", "models_dev", "api.json")


def validate_catalog(data: Any) -> dict[str, Any]:
    """校验 `api.json` 的顶层结构；不合法抛 ValueError。

    只做结构校验（不做逐字段 schema 校验）：models.dev 会持续加字段，过严的
    校验会让一次上游变更直接把本地目录废掉。
    """
    if not isinstance(data, dict):
        raise ValueError(f"models.dev 顶层不是对象（{type(data).__name__}）")
    if not data:
        raise ValueError("models.dev 目录为空")
    for provider_id, entry in data.items():
        if not isinstance(entry, dict):
            raise ValueError(f"models.dev provider '{provider_id}' 不是对象")
        models = entry.get("models")
        if models is not None and not isinstance(models, dict):
            raise ValueError(f"models.dev provider '{provider_id}' 的 models 不是对象")
    return data


def _read_cache_file() -> dict[str, Any]:
    """读取磁盘缓存（不存在/损坏/超大都抛错，由调用方决定降级）。"""
    path = cache_path()
    try:
        size = os.path.getsize(path)
    except OSError as exc:
        raise FileNotFoundError(f"models.dev 缓存不存在: {path}") from exc
    if size > MAX_BYTES:
        raise ValueError(f"models.dev 缓存过大（{size} 字节 > {MAX_BYTES}）: {path}")
    with open(path, "rb") as handle:
        raw = handle.read(MAX_BYTES + 1)
    if len(raw) > MAX_BYTES:
        raise ValueError(f"models.dev 缓存过大（> {MAX_BYTES} 字节）: {path}")
    data = json.loads(raw.decode("utf-8"))
    return validate_catalog(data)


def _write_cache_atomic(payload: bytes) -> None:
    """原子写缓存：临时文件 + `os.replace`，避免半截 JSON 覆盖掉可用缓存。"""
    path = cache_path()
    os.makedirs(os.path.dirname(path), exist_ok=True)
    tmp = f"{path}.{os.getpid()}.tmp"
    with open(tmp, "wb") as handle:
        handle.write(payload)
        handle.flush()
        os.fsync(handle.fileno())
    os.replace(tmp, path)


def _cache_age_seconds() -> float | None:
    """磁盘缓存距今多少秒；不存在时返回 None。"""
    try:
        return max(0.0, time.time() - os.path.getmtime(cache_path()))
    except OSError:
        return None


def _set_catalog(data: dict[str, Any], *, source: str) -> dict[str, Any]:
    global _catalog, _catalog_loaded_at, _catalog_source, _last_error
    _catalog = data
    _catalog_loaded_at = time.time()
    _catalog_source = source
    _last_error = ""
    return data


# ── 下载 ──


async def refresh_catalog() -> dict[str, Any]:
    """强制下载并解析目录，成功后原子写缓存。

    失败（网络/HTTP/非 JSON/超大/结构非法）抛 ValueError，旧内存目录与旧磁盘
    缓存都保持不变——调用方可以继续用陈旧数据工作。
    """
    global _last_error
    import httpx

    async with _refresh_lock:
        try:
            async with httpx.AsyncClient(timeout=60.0, follow_redirects=True) as client:
                response = await client.get(
                    MODELS_DEV_URL, headers={"User-Agent": "faustbot/3 (models.dev)"}
                )
                response.raise_for_status()
                payload = response.content
        except Exception as exc:  # noqa: BLE001 - 统一转成可展示的错误
            _last_error = f"下载 models.dev 目录失败: {exc}"
            log.warning(_last_error)
            raise ValueError(_last_error) from exc
        if len(payload) > MAX_BYTES:
            _last_error = f"models.dev 响应过大（{len(payload)} 字节 > {MAX_BYTES}）"
            log.warning(_last_error)
            raise ValueError(_last_error)
        try:
            # 解析放到线程里：全量 api.json 解析会占住事件循环几十毫秒以上。
            data = await asyncio.to_thread(json.loads, payload.decode("utf-8"))
            catalog = validate_catalog(data)
        except Exception as exc:  # noqa: BLE001
            _last_error = f"解析 models.dev 目录失败: {exc}"
            log.warning(_last_error)
            raise ValueError(_last_error) from exc
        try:
            await asyncio.to_thread(_write_cache_atomic, payload)
        except OSError as exc:
            # 写缓存失败不影响本次使用：内存目录仍然可用。
            log.warning("写入 models.dev 缓存失败（本次仍可使用内存目录）: %s", exc)
        log.info(
            "models.dev 目录已刷新: %d 个 provider（%.1f KB）",
            len(catalog),
            len(payload) / 1024.0,
        )
        return _set_catalog(catalog, source="network")


async def ensure_catalog(*, allow_network: bool = True, force: bool = False) -> dict[str, Any]:
    """取得当前可用的目录（内存 → 磁盘 → 网络）。

    Args:
        allow_network: 允许在内存/磁盘都没有可用目录时联网下载。
        force: 忽略内存/磁盘缓存，强制联网刷新（失败时回退到已有缓存）。

    返回空 dict 表示「当前没有目录」，调用方应回退到通用启发式，而不是报错。
    """
    global _last_error

    if not force and _catalog and (time.time() - _catalog_loaded_at) < CACHE_TTL_SECONDS:
        return _catalog

    if not force:
        try:
            data = await asyncio.to_thread(_read_cache_file)
        except Exception as exc:  # noqa: BLE001 - 缓存缺失/损坏都退化为「没有目录」
            if _catalog:
                # 内存里有（可能已过期）的目录：继续用，不因为磁盘缓存坏掉就失去它
                return _catalog
            log.debug("读取 models.dev 磁盘缓存失败: %s", exc)
            data = None
        if data is not None:
            return _set_catalog(data, source="cache")

    if not allow_network:
        if _catalog:
            return _catalog
        return {}

    try:
        return await refresh_catalog()
    except ValueError:
        # 刷新失败：能拿到陈旧缓存就继续用（明确告知年龄），否则返回空目录。
        try:
            data = await asyncio.to_thread(_read_cache_file)
        except Exception:  # noqa: BLE001
            if _catalog:
                return _catalog
            return {}
        age = _cache_age_seconds()
        log.warning(
            "models.dev 目录刷新失败，继续使用陈旧缓存（约 %.1f 小时前）: %s",
            (age or 0) / 3600.0,
            _last_error or "未知错误",
        )
        return _set_catalog(data, source="cache")


async def refresh_if_stale() -> dict[str, Any]:
    """后台刷新入口：目录不存在或已过期才联网；任何失败只记日志。

    供运行时重建时 `asyncio.create_task(...)` 调用，因此**绝不抛错**。
    """
    age = _cache_age_seconds()
    if _catalog and (time.time() - _catalog_loaded_at) < CACHE_TTL_SECONDS:
        return _catalog
    if age is not None and age < CACHE_TTL_SECONDS:
        return await ensure_catalog(allow_network=False)
    try:
        return await ensure_catalog(force=True)
    except Exception as exc:  # noqa: BLE001 - 后台任务不能把错误抛给事件循环
        log.warning("后台刷新 models.dev 目录失败: %s", exc)
        return _catalog or {}


def catalog_status() -> dict[str, Any]:
    """目录缓存状态（管理端 `/models-dev/status` 使用）。"""
    providers = len(_catalog) if isinstance(_catalog, dict) else 0
    return {
        "loaded": bool(_catalog),
        "source": _catalog_source,
        "providers": providers,
        "memory_age_seconds": (
            max(0.0, time.time() - _catalog_loaded_at) if _catalog_loaded_at else None
        ),
        "cache_path": cache_path(),
        "cache_age_seconds": _cache_age_seconds(),
        "cache_ttl_seconds": CACHE_TTL_SECONDS,
        "last_error": _last_error,
        "url": MODELS_DEV_URL,
    }


# ── 匹配 ──


@dataclass(frozen=True)
class CatalogMatch:
    """用户 Provider → models.dev provider 的匹配结果。"""

    provider_id: str
    host_matched: bool
    overlap: float
    score: float
    candidates: tuple[dict[str, Any], ...] = ()


def _host(url: str | None) -> str:
    """取出主机名（小写、不含端口）。无法解析时返回空串。"""
    text = str(url or "").strip()
    if not text:
        return ""
    if "//" not in text:
        text = "//" + text
    try:
        return (urlparse(text).hostname or "").lower()
    except ValueError:
        return ""


def _name_similarity(name: str, entry: dict[str, Any]) -> float:
    """Provider 名称与目录 id/name 的 token 相似度（0~1，仅作次级排序权重）。

    只做去分隔符/小写后的「交集 ÷ 较大集合大小」，不做任何具体名字特判。
    """
    import re

    def tokens(value: str) -> set[str]:
        return {t for t in re.split(r"[^a-z0-9]+", str(value or "").lower()) if t}

    left = tokens(name)
    right = tokens(entry.get("id") or "") | tokens(entry.get("name") or "")
    if not left or not right:
        return 0.0
    return len(left & right) / float(max(len(left), len(right)))


def _overlap(user_models: set[str], catalog_models: set[str]) -> tuple[float, int]:
    """返回 (覆盖率, 重合模型数)。

    覆盖率 = |用户模型 ∩ 目录模型| / |用户模型|，**不是 Jaccard**：
    用户通常只启用目录里的一部分模型（例如订阅只开了 20 个），Jaccard 会因为
    目录条目很大而把重合度压到 0.1 以下，导致永远匹配不上。覆盖率回答的是
    「目录能解释我配置的多少模型」，这正是我们需要的信号。
    """
    if not user_models or not catalog_models:
        return 0.0, 0
    matched = len(user_models & catalog_models)
    return matched / float(len(user_models)), matched



def _catalog_provider_api(entry: dict[str, Any]) -> str:
    api = entry.get("api")
    return str(api) if isinstance(api, str) else ""


def match_provider(provider: Any, catalog: dict[str, Any]) -> CatalogMatch | None:
    """把用户 Provider 匹配到目录条目；无候选时返回 None（不改动配置）。

    打分规则（通用，无 Provider 特判）：

    - host 相同：`2 + 覆盖率 + 0.2 * name_sim`（host 是强信号）；
    - host 不同：只有「覆盖率 ≥ 0.8 且至少 2 个模型重合」才作为候选，
      `覆盖率 + 0.3 * name_sim`——这类匹配只用于取「格式」，端点仍用用户配置。

    覆盖率 = 目录能解释的用户模型比例（见 `_overlap`），不用 Jaccard：
    用户通常只启用目录的一部分模型。
    """
    if not catalog:
        return None
    user_models = {str(m) for m in (getattr(provider, "models", None) or []) if str(m)}
    user_host = _host(getattr(provider, "base_url", "") or "")
    pinned = str(getattr(provider, "catalog_provider_id", "") or "").strip()

    if pinned and isinstance(catalog.get(pinned), dict):
        entry = catalog[pinned]
        entry_models = set((entry.get("models") or {}).keys())
        host_matched = bool(user_host) and user_host == _host(_catalog_provider_api(entry))
        overlap, _matched = _overlap(user_models, entry_models)
        return CatalogMatch(
            provider_id=pinned,
            host_matched=host_matched,
            overlap=overlap,
            score=0.0 if not host_matched else 2.0,
            candidates=(),
        )

    host_candidates: list[tuple[float, CatalogMatch]] = []
    name_candidates: list[tuple[float, CatalogMatch]] = []
    name = str(getattr(provider, "name", "") or "")
    for provider_id, entry in catalog.items():
        if not isinstance(entry, dict):
            continue
        entry_models = set((entry.get("models") or {}).keys())
        overlap, matched = _overlap(user_models, entry_models)
        name_sim = _name_similarity(name, {"id": provider_id, "name": entry.get("name")})
        host_match = bool(user_host) and user_host == _host(_catalog_provider_api(entry))
        match = CatalogMatch(
            provider_id=provider_id,
            host_matched=host_match,
            overlap=overlap,
            score=(2.0 + overlap + 0.2 * name_sim) if host_match else 0.0,
            candidates=(),
        )
        if host_match:
            host_candidates.append((match.score, match))
        elif overlap >= MIN_OVERLAP and matched >= MIN_OVERLAP_MODELS:
            match = CatalogMatch(
                provider_id=provider_id,
                host_matched=False,
                overlap=overlap,
                score=overlap + 0.3 * name_sim,
                candidates=(),
            )
            name_candidates.append((match.score, match))

    pool = host_candidates or name_candidates
    if not pool:
        return None
    pool.sort(key=lambda item: (item[0], item[1].overlap), reverse=True)
    best = pool[0][1]
    top = tuple(
        {
            "provider_id": item.provider_id,
            "host_matched": item.host_matched,
            "overlap": round(item.overlap, 4),
            "score": round(item.score, 4),
        }
        for _score, item in pool[:5]
    )
    return CatalogMatch(
        provider_id=best.provider_id,
        host_matched=best.host_matched,
        overlap=best.overlap,
        score=best.score,
        candidates=top,
    )


# ── 路由 ──


@dataclass(frozen=True)
class CatalogRoute:
    """单个模型在目录里的通用路由信息。"""

    api_format: str
    base_url: str | None
    npm: str
    shape: str
    raw_api: str


def route_model(
    catalog: dict[str, Any],
    provider_id: str,
    model_id: str,
    *,
    host_matched: bool,
    provider_base_url: str = "",
) -> CatalogRoute | None:
    """从目录条目推算单个模型的 API 格式与端点；目录里没有该模型时返回 None。"""
    entry = catalog.get(provider_id) if catalog else None
    if not isinstance(entry, dict):
        return None
    models = entry.get("models") or {}
    model = models.get(model_id) if isinstance(models, dict) else None
    if not isinstance(model, dict):
        return None

    override = model.get("provider") if isinstance(model.get("provider"), dict) else {}
    npm = str(override.get("npm") or entry.get("npm") or "")
    shape = str(override.get("shape") or "")
    raw_api = str(override.get("api") or entry.get("api") or "")
    api_format = detect_api_format(npm=npm, shape=shape, api=raw_api)

    base_url: str | None = None
    if host_matched and raw_api:
        base_url = normalize_format_base_url(api_format, raw_api)
    elif not host_matched and provider_base_url:
        # host 不同：只采用格式，端点用用户自己的配置
        base_url = normalize_format_base_url(api_format, provider_base_url)
    return CatalogRoute(
        api_format=api_format,
        base_url=base_url,
        npm=npm,
        shape=shape,
        raw_api=raw_api,
    )


def enrich_provider(provider: Any, catalog: dict[str, Any]) -> dict[str, Any]:
    """用目录刷新单个 Provider 的缓存路由与模型限制（不改动用户显式配置）。

    写入字段：

    - `catalog_provider_id`：匹配到的目录 id（匹配不到则保持原样）；
    - `model_catalog_routes`：**整表重算**，只保留当前 `provider.models` 中的模型；
      每条路由带上 `thinking`（思考参数形状，来自 `reasoning_options`）；
    - `model_context_lengths` / `model_max_output_tokens`：只填空缺，不覆盖已有值；
    - `catalog_synced_at`：本次刷新时间。

    返回统计信息供管理端展示（匹配结果、已路由数量、目录缺失的模型）。
    """
    result: dict[str, Any] = {
        "provider": str(getattr(provider, "name", "") or ""),
        "provider_id": None,
        "host_matched": False,
        "overlap": 0.0,
        "routed": 0,
        "missing": [],
        "formats": {},
        "candidates": [],
    }
    if not catalog:
        result["missing"] = list(getattr(provider, "models", None) or [])
        return result

    match = match_provider(provider, catalog)
    if match is None:
        result["missing"] = list(getattr(provider, "models", None) or [])
        log.info(
            "Provider '%s' 未匹配到 models.dev 目录（%d 个模型），格式将回退到通用规则",
            getattr(provider, "name", ""),
            len(getattr(provider, "models", None) or []),
        )
        return result

    provider.catalog_provider_id = match.provider_id
    result["provider_id"] = match.provider_id
    result["host_matched"] = match.host_matched
    result["overlap"] = round(match.overlap, 4)
    result["candidates"] = list(match.candidates)

    entry = catalog.get(match.provider_id) or {}
    entry_models = entry.get("models") if isinstance(entry.get("models"), dict) else {}
    base_url = str(getattr(provider, "base_url", "") or "")

    routes: dict[str, dict[str, Any]] = {}
    missing: list[str] = []
    for model_id in list(getattr(provider, "models", None) or []):
        route = route_model(
            catalog,
            match.provider_id,
            model_id,
            host_matched=match.host_matched,
            provider_base_url=base_url,
        )
        model = entry_models.get(model_id) if isinstance(entry_models, dict) else None
        model_reasoning = (
            model.get("reasoning") if isinstance(model, dict) else None
        )
        if route is None:
            missing.append(model_id)
            continue
        routes[model_id] = {
            "format": route.api_format,
            "base_url": route.base_url,
            "host_matched": match.host_matched,
            "npm": route.npm,
            "shape": route.shape,
            # models.dev 明确标注不支持推理时（False），构建模型时不加思考参数，
            # 避免对非推理模型发 thinking 导致 400；未知（None）时保留用户设置。
            "reasoning": model_reasoning if isinstance(model_reasoning, bool) else None,
            # 思考参数形状（effort / budget_tokens / toggle）：Gemini 3/4 只接受
            # thinking level，2.5 只接受 thinking budget，必须由数据决定而不是猜。
            "thinking": capability_from_catalog_model(model),
        }
        if match.host_matched or base_url:
            result["formats"][model_id] = route.api_format

        limit = model.get("limit") if isinstance(model, dict) else None
        if isinstance(limit, dict):
            context = limit.get("context")
            output = limit.get("output")
            lengths = getattr(provider, "model_context_lengths", None)
            if isinstance(lengths, dict) and isinstance(context, int) and context > 0:
                if not lengths.get(model_id):
                    lengths[model_id] = int(context)
            outputs = getattr(provider, "model_max_output_tokens", None)
            if isinstance(outputs, dict) and isinstance(output, int) and output > 0:
                if not outputs.get(model_id):
                    outputs[model_id] = int(output)

    provider.model_catalog_routes = routes
    provider.catalog_synced_at = time.time()
    result["routed"] = len(routes)
    result["missing"] = missing

    if not match.host_matched:
        log.info(
            "Provider '%s' 匹配到目录 '%s'（仅模型名重合，overlap=%.2f）："
            "采用目录格式但端点仍用用户 base_url %s",
            getattr(provider, "name", ""),
            match.provider_id,
            match.overlap,
            base_url,
        )
    return result


def enrich_all(providers: Any, catalog: dict[str, Any]) -> list[dict[str, Any]]:
    """对全部 Provider 应用目录刷新（同步；管理端负责保存配置）。"""
    return [enrich_provider(provider, catalog) for provider in (providers.providers or [])]


def summarize_formats(routes: dict[str, dict[str, Any]]) -> dict[str, int]:
    """统计某个 Provider 各格式的模型数量（UI 概览用）。"""
    counts: dict[str, int] = {fmt: 0 for fmt in API_FORMATS}
    for route in (routes or {}).values():
        fmt = str((route or {}).get("format") or "")
        if fmt in counts:
            counts[fmt] += 1
    return counts
