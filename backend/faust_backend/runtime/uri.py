"""
URI multi-scheme parser for FaustBot harness.

Supports:
  - file paths:           src/main.py, src/main.py:50-100, src/
  - artifact references:  artifact://abc123, artifact://abc123:50-100
  - memory references:    memory://notes/math, memory://notes/math:50-100
    - skill references:     skill://slug/SKILL.md
    - faustbot references:  faustbot://index.md
    - sourceCode references: sourceCode://backend/main.py, sourceCode://backend/ (目录自动列目录)
  - raw selector:         src/main.py:raw、skill://slug/scripts/a.py:raw（关闭结构化摘要；
                          可单独用，也可追加在行选择器后，如 :50-100:raw）
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

# 支持的形式：
#   :N / :-N                         单行（负数 = 从末尾倒数）
#   :A-B / :-A--B / :-A-B / :A--B   范围（任一端可为负数，从末尾倒数）
#   :-A:-B                           双冒号负范围（-20:-10 = 倒数第20行~倒数第10行）
#   :A+C / :-A+C                     C 行从 A 起
#   :raw                             关闭结构化摘要（源码文件返回原文）;可单独使用，
#                                    也可追加在行选择器后（:50-100:raw，行选择本就不做摘要）
SELECTOR_RE = re.compile(r"^(?::raw|:-?\d+(?:(?::|-)-?\d+|\+-?\d+)?(:raw)?)$")
SCHEME_ARTIFACT = "artifact"
SCHEME_MEMORY = "memory"
SCHEME_SKILL = "skill"
SCHEME_FAUSTBOT = "faustbot"
SCHEME_IMG_SOURCE = "img_source"
SCHEME_SOURCE_CODE = "sourcecode"
SCHEME_FILE = "file"

# 全部已知协议前缀（小写）
_ALL_SCHEMES = (
    SCHEME_ARTIFACT,
    SCHEME_MEMORY,
    SCHEME_SKILL,
    SCHEME_FAUSTBOT,
    SCHEME_IMG_SOURCE,
    SCHEME_SOURCE_CODE,
)


def detect_unsupported_protocol(uri: str, supported: set[str]) -> str | None:
    """检测 URI 是否带了当前工具不支持的协议前缀。

    - ``uri`` 带 ``xxx://`` 前缀且 scheme 不在 ``supported`` 中 →
      返回形如 ``"skill:// 协议不支持 search（支持: faustbot, memory）"`` 的错误文本；
    - 带支持的协议前缀或裸路径 → 返回 None（继续正常处理）。
    """
    lower = str(uri or "").strip().lower()
    for scheme in _ALL_SCHEMES:
        if lower.startswith(f"{scheme}://"):
            if scheme not in supported:
                supported_list = sorted(
                    s for s in supported if s != SCHEME_FILE
                ) or [SCHEME_FILE]
                return (
                    f"{scheme}:// 协议不支持此操作"
                    f"（支持: {', '.join(f'{s}://' for s in supported_list)} 或直接写文件路径）"
                )
            return None
    return None


@dataclass
class ParsedURI:
    scheme: str  # "file" | "artifact" | "memory" | "skill" | "faustbot" | "img_source"
    path: str  # normalized path (no selector, no query)
    selector: str | None  # ":50-100", ":50+20", ":raw" — or None
    query: dict[str, list[str]]  # parsed query params (only memory://)
    trailing_slash: bool = False  # 原始 URI 是否以 / 结尾（区分目录）

    @property
    def selector_lines(self) -> tuple[int, int] | None:
        """Parse selector as (start_line, end_line) 1-indexed inclusive.

        ``:raw``（单独使用）没有行范围 → None。
        """
        if not self.selector:
            return None
        s = self.selector.lstrip(":")
        if s == "raw":
            return None
        if s.endswith(":raw"):
            s = s[:-4]
        if not s:
            return None
        # 双冒号负范围：:-20:-10 → 倒数第20行 ~ 倒数第10行
        if ":" in s and not s.startswith("+") and not s.startswith(":"):
            a, b = s.split(":", 1)
            try:
                return (int(a), int(b))
            except ValueError:
                pass
        if "+" in s and not s.startswith("-"):
            offset, length = s.split("+", 1)
            start = int(offset)
            return (start, start + int(length) - 1)
        range_match = re.match(r"^(-?\d+)-(-?\d+)$", s)
        if range_match:
            return (int(range_match.group(1)), int(range_match.group(2)))
        n = int(s)
        return (n, n)

    @property
    def is_raw(self) -> bool:
        """True when the selector requests raw output (关闭结构化摘要)。"""
        if not self.selector:
            return False
        return self.selector == ":raw" or self.selector.endswith(":raw")

    @property
    def is_dir(self) -> bool:
        """True if path is empty or the URI ends with / (and has no line selector).

        仅带 ``:raw`` 时不改变目录语义：``src/:raw`` 仍算目录。
        """
        return self.selector_lines is None and (self.path == "" or self.trailing_slash)


def _extract_selector(rest: str) -> str | None:
    """Try to match a selector suffix (line range) from `rest`.

    Starts with the entire rest after the first colon, then progressively
    shrinks from the left until a match is found or no colons remain.
    This handles selectors with embedded colons like ':50-100:raw'.

    Negative ranges use an extra colon to separate the two endpoints
    (e.g. ':-20:-10' = lines 20 from end through 10 from end), so the
    longest matching candidate wins — otherwise ':-10' would be picked
    and ':-20' would leak back into the path.
    """
    colon_positions = [i for i, ch in enumerate(rest) if ch == ":"]
    best = None
    for pos in reversed(colon_positions):
        candidate = rest[pos:]
        if SELECTOR_RE.match(candidate):
            # Prefer the longest match so ':-20:-10' keeps both endpoints
            if best is None or len(candidate) > len(best):
                best = candidate
    return best


def parse(uri: str) -> ParsedURI:
    """Parse a URI string into its scheme, path, selector and query components.

    Input       → scheme     path              selector

    ─────────────────────────────────────────────────────
    
    src/main.py → file       src/main.py       None

    src/main.py:50-100 → file  src/main.py    :50-100

    src/        → file       src/              None

    artifact://abc123        → artifact  abc123         None

    artifact://abc123:50-100 → artifact  abc123         :50-100

    memory://notes/math      → memory    notes/math     None

    memory://notes/math:50-100 → memory  notes/math     :50-100

    memory://   → memory     ""                None

    """
    raw = str(uri or "").strip()
    if not raw:
        return ParsedURI(scheme=SCHEME_FILE, path="", selector=None, query={})

    # Detect scheme prefix (case-insensitive: sourceCode:// == sourcecode://)
    lower_raw = raw.lower()
    for scheme in (SCHEME_ARTIFACT, SCHEME_MEMORY, SCHEME_SKILL, SCHEME_FAUSTBOT, SCHEME_IMG_SOURCE, SCHEME_SOURCE_CODE):
        prefix = f"{scheme}://"
        if lower_raw.startswith(prefix):
            rest = raw[len(prefix):]
            # Split off selector and query
            selector = None
            query: dict[str, list[str]] = {}

            # Query first (after ?, before #)
            qpos = rest.find("?")
            if qpos > -1:
                parsed = urlparse(raw)
                query = {k: v for k, v in parse_qs(parsed.query).items()}
                rest = rest[:qpos]

            # Selector: try progressively longer candidates from rightmost colon
            if ":" in rest:
                selector = _extract_selector(rest)
                if selector:
                    rest = rest[:len(rest) - len(selector)]

            trailing_slash = rest.endswith("/")
            path = rest.strip("/")
            return ParsedURI(scheme=scheme, path=path, selector=selector, query=query,
                             trailing_slash=trailing_slash)

    # Bare file path
    selector = None
    rest = raw

    if ":" in rest:
        candidate = _extract_selector(rest)
        if candidate and not re.match(r"^[a-zA-Z]$", rest[:len(rest) - len(candidate)]):
            selector = candidate
            rest = rest[:len(rest) - len(candidate)]

    # Normalize
    path = rest.replace("\\", "/").strip()
    trailing_slash = path.endswith("/")
    return ParsedURI(scheme=SCHEME_FILE, path=path, selector=selector, query={},
                     trailing_slash=trailing_slash)
def resolve(path: str, base_dir: str | None = None) -> str:
    """Resolve a relative path to an absolute one."""
    p = Path(path.replace("\\", "/"))
    if p.is_absolute():
        return str(p)
    if base_dir:
        return str((Path(base_dir) / p).resolve())
    return str(p.resolve())
