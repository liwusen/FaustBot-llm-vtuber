# backend/faust_backend/memory/tokenize_pool.py
"""jieba 分词进程池：分词是 CPU 密集且首载词典慢的操作，
放到单独进程异步执行，避免阻塞事件循环（用户硬性要求）。

max_workers=1：单一常驻 worker，词典只加载一次（进程级缓存），并保证批次顺序。
"""
import asyncio
from concurrent.futures import ProcessPoolExecutor

_executor: ProcessPoolExecutor | None = None


def _jieba_worker(texts: list[str]) -> list[list[str]]:
    """进程池 worker。必须保持模块级可 pickle；jieba 在子进程内惰性加载。"""
    import jieba

    jieba.setLogLevel(60)
    return [list(jieba.cut_for_search(t)) for t in texts]


def _get_executor() -> ProcessPoolExecutor:
    global _executor
    if _executor is None or _executor._broken:  # type: ignore[attr-defined]
        _executor = ProcessPoolExecutor(max_workers=1)
    return _executor


async def jieba_tokenize_batch(texts: list[str]) -> list[list[str]]:
    loop = asyncio.get_running_loop()
    return await loop.run_in_executor(_get_executor(), _jieba_worker, list(texts))


async def jieba_tokenize(text: str) -> list[str]:
    return (await jieba_tokenize_batch([text]))[0]
