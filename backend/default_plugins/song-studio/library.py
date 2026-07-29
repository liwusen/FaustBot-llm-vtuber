"""曲库扫描与缓存管理（主环境运行，无重依赖）。"""
from __future__ import annotations

import hashlib
import json
import time
from pathlib import Path

AUDIO_EXTS = {".mp3", ".wav", ".flac", ".m4a", ".ogg"}


def file_sha1(path: Path) -> str:
    h = hashlib.sha1()
    with open(path, "rb") as f:
        for chunk in iter(lambda: f.read(1024 * 1024), b""):
            h.update(chunk)
    return h.hexdigest()


def cache_key(song_sha1: str, ref_sha1: str, params: dict) -> str:
    params_json = json.dumps(params, sort_keys=True, ensure_ascii=False)
    return hashlib.sha1(f"{song_sha1}:{ref_sha1}:{params_json}".encode("utf-8")).hexdigest()[:16]


class SongLibrary:
    """管理 source 目录扫描与 cache 目录状态。"""

    def __init__(self, data_dir: Path):
        self.data_dir = data_dir
        self.source_dir = data_dir / "library" / "source"
        self.cache_dir = data_dir / "cache"
        self.refs_dir = data_dir / "refs"
        for d in (self.source_dir, self.cache_dir, self.refs_dir):
            d.mkdir(parents=True, exist_ok=True)
        self._sha_cache: dict[str, tuple[float, str]] = {}

    def _sha1_cached(self, path: Path) -> str:
        stat = path.stat()
        key = str(path)
        cached = self._sha_cache.get(key)
        if cached and cached[0] == stat.st_mtime:
            return cached[1]
        digest = file_sha1(path)
        self._sha_cache[key] = (stat.st_mtime, digest)
        return digest

    def list_source_songs(self) -> list[dict]:
        songs = []
        for path in sorted(self.source_dir.iterdir()):
            if not path.is_file() or path.suffix.lower() not in AUDIO_EXTS:
                continue
            lrc = path.with_suffix(".lrc")
            songs.append({
                "name": path.stem,
                "file": str(path),
                "size": path.stat().st_size,
                "lrc": str(lrc) if lrc.exists() else None,
            })
        return songs

    def find_song(self, name: str) -> dict | None:
        name_lower = name.strip().lower()
        songs = self.list_source_songs()
        for song in songs:
            if song["name"].lower() == name_lower:
                return song
        for song in songs:
            if name_lower in song["name"].lower():
                return song
        return None

    def cache_entry(self, song_file: Path, ref_file: Path, params: dict) -> dict:
        """返回 {key, dir, final, meta, ready}。"""
        song_sha = self._sha1_cached(song_file)
        ref_sha = self._sha1_cached(ref_file)
        key = cache_key(song_sha, ref_sha, params)
        entry_dir = self.cache_dir / key
        final = entry_dir / "final.wav"
        meta = entry_dir / "meta.json"
        return {
            "key": key,
            "dir": entry_dir,
            "final": final,
            "meta": meta,
            "ready": final.exists() and meta.exists(),
        }

    def write_meta(self, entry: dict, song: dict, ref_file: Path, params: dict, elapsed_sec: float) -> None:
        entry["dir"].mkdir(parents=True, exist_ok=True)
        payload = {
            "song": song["name"],
            "source": song["file"],
            "reference": str(ref_file),
            "params": params,
            "elapsed_sec": round(elapsed_sec, 1),
            "created_at": int(time.time()),
        }
        entry["meta"].write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")

    def delete_cache(self, key: str) -> bool:
        import shutil
        target = self.cache_dir / key
        if not target.is_dir():
            return False
        shutil.rmtree(target)
        return True
