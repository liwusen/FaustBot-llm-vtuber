"""
URI multi-scheme parser for FaustBot harness.

Supports:
  - file paths:           src/main.py, src/main.py:50-100, src/
  - artifact references:  artifact://abc123, artifact://abc123:50-100
  - memory references:    memory://notes/math, memory://notes/math:50-100
"""

from __future__ import annotations

import re
from dataclasses import dataclass, field
from pathlib import Path
from urllib.parse import parse_qs, urlparse

SELECTOR_RE = re.compile(r"^:\d+([+-]\d+)?(-\d+)?(:raw)?$")
SCHEME_ARTIFACT = "artifact"
SCHEME_MEMORY = "memory"
SCHEME_FILE = "file"


@dataclass
class ParsedURI:
    scheme: str  # "file" | "artifact" | "memory"
    path: str  # normalized path (no selector, no query)
    selector: str | None  # ":50-100", ":50+20", ":raw" — or None
    query: dict[str, list[str]]  # parsed query params (only memory://)

    @property
    def selector_lines(self) -> tuple[int, int] | None:
        """Parse selector as (start_line, end_line) 1-indexed inclusive."""
        if not self.selector:
            return None
        s = self.selector.lstrip(":")
        raw = s.endswith(":raw")
        if raw:
            s = s[:-4]
        if "+" in s:
            offset, length = s.split("+", 1)
            start = int(offset)
            return (start, start + int(length) - 1)
        elif "-" in s:
            parts = s.split("-", 1)
            if not parts[0]:
                return (1, int(parts[1]))
            start = int(parts[0])
            end = int(parts[1]) if parts[1] else None
            return (start, end) if end else (start, start)
        else:
            n = int(s)
            return (n, n)

    @property
    def is_dir(self) -> bool:
        """True if path ends with / and has no selector."""
        return not self.selector and (self.path == "" or self.path.endswith("/"))


def _extract_selector(rest: str) -> str | None:
    """Try to match a selector suffix (line range) from `rest`.

    Starts with the entire rest after the first colon, then progressively
    shrinks from the left until a match is found or no colons remain.
    This handles selectors with embedded colons like ':50-100:raw'.
    """
    colon_positions = [i for i, ch in enumerate(rest) if ch == ":"]
    for pos in reversed(colon_positions):
        candidate = rest[pos:]
        if SELECTOR_RE.match(candidate):
            return candidate
    return None


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

    # Detect scheme prefix
    for scheme in (SCHEME_ARTIFACT, SCHEME_MEMORY):
        prefix = f"{scheme}://"
        if raw.startswith(prefix):
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

            path = rest.strip("/")
            return ParsedURI(scheme=scheme, path=path, selector=selector, query=query)

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
    return ParsedURI(scheme=SCHEME_FILE, path=path, selector=selector, query={})
def resolve(path: str, base_dir: str | None = None) -> str:
    """Resolve a relative path to an absolute one."""
    p = Path(path.replace("\\", "/"))
    if p.is_absolute():
        return str(p)
    if base_dir:
        return str((Path(base_dir) / p).resolve())
    return str(p.resolve())
