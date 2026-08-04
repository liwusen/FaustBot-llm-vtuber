import functools
import subprocess
import sys
import time
from typing import Callable
import logging
import threading
from pathlib import Path
import requests
from faust_backend.logger import get_logger

log = get_logger("faust.utils")

def show_return_wrapper(func: Callable):
    @functools.wraps(func)
    async def wrapper(*args, **kwargs):
        result = await func(*args, **kwargs)
        print(f"Returning from {func.__name__}:", result)
        return result

    return wrapper


class CrossPlatformClipboard:
    def __init__(self):
        self.system = sys.platform

    def copy(self, text):
        """跨平台复制文本到剪切板"""
        if self.system == "win32":
            try:
                import pyperclip

                pyperclip.copy(text)
            except ImportError:
                # 使用Windows命令行工具
                subprocess.run(["clip"], input=text, text=True, check=True)
        elif self.system == "darwin":  # macOS
            subprocess.run(["pbcopy"], input=text, text=True, check=True)
        elif self.system.startswith("linux"):  # Linux
            try:
                subprocess.run(
                    ["xclip", "-selection", "clipboard"],
                    input=text,
                    text=True,
                    check=True,
                )
            except FileNotFoundError:
                subprocess.run(
                    ["xsel", "--clipboard", "--input"],
                    input=text,
                    text=True,
                    check=True,
                )

    def paste(self):
        """跨平台从剪切板粘贴文本"""
        if self.system == "win32":
            try:
                import pyperclip

                return pyperclip.paste()
            except ImportError:
                # 使用PowerShell
                result = subprocess.run(
                    ["powershell", "-command", "Get-Clipboard"],
                    capture_output=True,
                    text=True,
                )
                return result.stdout.strip()
        elif self.system == "darwin":  # macOS
            result = subprocess.run(["pbpaste"], capture_output=True, text=True)
            return result.stdout
        elif self.system.startswith("linux"):  # Linux
            try:
                result = subprocess.run(
                    ["xclip", "-selection", "clipboard", "-o"],
                    capture_output=True,
                    text=True,
                )
                return result.stdout
            except FileNotFoundError:
                result = subprocess.run(
                    ["xsel", "--clipboard", "--output"], capture_output=True, text=True
                )
                return result.stdout


class PerfTimer:

    def __init__(self, names=None):
        self._timers = {}
        self._active = {}
        self._order = names or []

    def begin(self, name):
        if name not in self._order:
            self._order.append(name)
        self._active[name] = time.perf_counter()

    def end(self, name):
        t = time.perf_counter()
        start = self._active.pop(name, None)
        delta = t - start if start is not None else 0
        self._timers[name] = self._timers.get(name, 0) + delta
        return delta

    def drain(self):
        for name in list(self._active.keys()):
            self.end(name)

    def get(self, name):
        return self._timers.get(name, 0)

    def total(self):
        return sum(self._timers.values())

    def itemize(self):
        items = []
        total = self.total()
        for name in self._order:
            ms = self._timers.get(name, 0) * 1000
            items.append(f"{name}:{ms:.0f}ms")
        items.append(f"= {total * 1000:.0f}ms")
        return " ".join(items)

    def print_pref(self, extra=""):
        line = self.itemize()
        if extra:
            line = f"{line} | {extra}"
        print(line)
        self.reset(keep_order=True)

    def __str__(self):
        line = self.itemize()
        self.reset(keep_order=True)
        return line

    def reset(self, keep_order=True):
        self._timers.clear()
        self._active.clear()
        if not keep_order:
            self._order.clear()

    def log_pref(self, logger: logging.Logger, extra: str = ""):
        line = self.itemize()
        if extra:
            line = f"{line} | {extra}"
        logger.info(line)
        self.reset(keep_order=True)

RELEASE_CACHE_HOURS = 1
DOWNLOAD_THREADS = 32
DOWNLOAD_CHUNK_SIZE = 8192 * 16
DOWNLOAD_MAX_RETRIES = 3
DOWNLOAD_RETRY_BASE_DELAY = 2.0
class DownloadTask:
    def __init__(
        self,
        url: str,
        dest_path: Path,
        tag: str,
        asset_name: str,
        num_threads: int = DOWNLOAD_THREADS,
    ):
        self.url = url
        self.dest_path = dest_path
        self.tag = tag
        self.asset_name = asset_name
        self.num_threads = num_threads
        self.total_bytes: int = 0
        self.downloaded_bytes: int = 0
        self.a = threading.Lock()
        self._start_time: float = 0.0
        self._done = False
        self._error: str | None = None
        self._progress_cb: Callable | None = None

    @property
    def elapsed(self) -> float:
        return time.time() - self._start_time if self._start_time else 0.0

    @property
    def speed_mbps(self) -> float:
        e = self.elapsed
        return (self.downloaded_bytes / 1_048_576) / e if e > 0 else 0.0

    @property
    def progress(self) -> float:
        if self.total_bytes == 0:
            return 0.0
        return min(1.0, self.downloaded_bytes / self.total_bytes)

    @property
    def done(self) -> bool:
        return self._done

    @property
    def error(self) -> str | None:
        return self._error

    def to_dict(self) -> dict:
        return {
            "tag": self.tag,
            "asset_name": self.asset_name,
            "total_bytes": self.total_bytes,
            "downloaded_bytes": self.downloaded_bytes,
            "total_mb": (
                round(self.total_bytes / 1_048_576, 1) if self.total_bytes else 0
            ),
            "downloaded_mb": round(self.downloaded_bytes / 1_048_576, 1),
            "progress": round(self.progress * 100, 1),
            "speed_mbps": round(self.speed_mbps, 1),
            "elapsed_sec": round(self.elapsed, 1),
            "done": self._done,
            "error": self._error,
        }

    def _request_with_retry(self, method: str, **kwargs) -> requests.Response:
        last_exc: Exception | None = None
        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                r = requests.request(method, self.url, **kwargs)
                r.raise_for_status()
                return r
            except Exception as e:
                last_exc = e
                if attempt < DOWNLOAD_MAX_RETRIES:
                    delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.warning(
                        f"Download attempt {attempt}/{DOWNLOAD_MAX_RETRIES} failed for "
                        f"{self.asset_name}, retrying in {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
        raise last_exc  # type: ignore[misc]

    def _dl_chunk_with_retry(self, start: int, end: int, idx: int) -> Path:
        part = self.dest_path.with_suffix(f".part{idx}")
        h = {"Range": f"bytes={start}-{end}"}
        last_exc: Exception | None = None
        for attempt in range(1, DOWNLOAD_MAX_RETRIES + 1):
            try:
                r = requests.get(self.url, headers=h, stream=True, timeout=120)
                r.raise_for_status()
                # 本 chunk 实际写入的字节数，只在成功后一次性累加，
                # 避免重试时 downloaded_bytes 重复计数（进度虚增到 100%）
                written = 0
                with open(part, "wb") as f:
                    for data in r.iter_content(DOWNLOAD_CHUNK_SIZE):
                        if data:
                            f.write(data)
                            written += len(data)
                with self._lock:
                    self.downloaded_bytes += written
                return part
            except Exception as e:
                last_exc = e
                if attempt < DOWNLOAD_MAX_RETRIES:
                    delay = DOWNLOAD_RETRY_BASE_DELAY * (2 ** (attempt - 1))
                    log.warning(
                        f"Chunk {idx} attempt {attempt}/{DOWNLOAD_MAX_RETRIES} failed "
                        f"({start}-{end}), retrying in {delay:.1f}s: {e}"
                    )
                    time.sleep(delay)
                if part.exists():
                    part.unlink()
        raise last_exc  # type: ignore[misc]

    def run(self) -> None:
        self._start_time = time.time()
        try:
            head = self._request_with_retry("HEAD", timeout=15)
            self.total_bytes = int(head.headers.get("content-length", 0))
            if self.total_bytes == 0:
                raise RuntimeError("无法获取文件大小")

            self.dest_path.parent.mkdir(parents=True, exist_ok=True)

            with open(self.dest_path, "wb") as f:
                f.truncate(self.total_bytes)

            chunk = self.total_bytes // self.num_threads
            ranges: list[tuple[int, int, int]] = []
            for i in range(self.num_threads):
                start = i * chunk
                end = (
                    start + chunk - 1
                    if i < self.num_threads - 1
                    else self.total_bytes - 1
                )
                if start <= end:
                    ranges.append((start, end, i))

            part_paths: list[Path] = []

            import concurrent.futures
            import shutil

            pool = concurrent.futures.ThreadPoolExecutor(
                max_workers=self.num_threads
            )
            fut_map = {
                pool.submit(self._dl_chunk_with_retry, s, e, i): i
                for s, e, i in ranges
            }
            # 注意：不能用 with 块——异常时 __exit__ 会 shutdown(wait=True)
            # 等待所有线程（含慢速重试的 chunk）结束，导致进度已满但
            # _done 迟迟不置位、前端卡在 100%。改为失败立即 shutdown(wait=False)。
            try:
                for fut in concurrent.futures.as_completed(fut_map):
                    exc = fut.exception()
                    if exc:
                        raise exc
                    part_paths.append(fut.result())
            except BaseException:
                pool.shutdown(wait=False, cancel_futures=True)
                raise
            finally:
                pool.shutdown(wait=True)

            part_paths.sort(key=lambda p: int(p.suffix.replace(".part", "")))
            with open(self.dest_path, "wb") as out:
                for pp in part_paths:
                    with open(pp, "rb") as f:
                        shutil.copyfileobj(f, out)
                    pp.unlink()

            self._done = True
        except Exception as e:
            self._error = str(e)