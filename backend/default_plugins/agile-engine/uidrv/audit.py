"""LimitedUI 审计：JSONL 逐步记录 + 会话汇总 + 截图留存/清理 + vfs 文本。

位置：``<plugin_data>/agile-engine/ui-ops/<模块名>/``（设计 §16）::

    ui-ops/<module>/
    ├── {YYYYMMDD}.jsonl          # 每步一行（type=session_start/step/session_end）
    ├── summary.json              # 会话级汇总
    ├── last_escalation.json      # 最后一次升级的完整细节
    └── frames/{step_id}-{阶段}.jpg

留存规则：升级/异常必存；另外每 ``snapshot_every`` 步存一张；超过 ``keep_days`` 天或 ``keep_images``
张时删最老的（会话启动与每 100 步各检查一次）。
"""
from __future__ import annotations

import json
import time
from pathlib import Path
from typing import Any, Optional

# 汇总里保留的问法键（避免无界增长）
_MAX_QUESTION_KEYS = 64


def _now_ts() -> float:
    return time.time()


class AuditWriter:
    """会话审计写入器（一个会话一个实例，模块名固定）。"""

    def __init__(self, module: str, root: Path, *, snapshot_every: int = 50,
                 keep_days: int = 3, keep_images: int = 500, session_id: str = "") -> None:
        self.module = str(module)
        self.dir = Path(root) / "ui-ops" / self.module
        self.frames_dir = self.dir / "frames"
        self.session_id = session_id or _now_ts().hex()
        self.snapshot_every = max(1, int(snapshot_every))
        self.keep_days = max(0, int(keep_days))
        self.keep_images = max(0, int(keep_images))
        self._stats: dict[str, Any] = {
            "session_id": self.session_id,
            "steps": 0,
            "injected": 0,
            "uncertain": 0,
            "escalations": 0,
            "frame_invalid": 0,
            "frame_changed_false": 0,
            "state_source": {"hook": 0, "model": 0},
            "questions": {},
            "started_at": _now_ts(),
        }
        self._recent: list[dict[str, Any]] = []
        self.last_frame: Optional[Path] = None
        self._warned: set[str] = set()

    # ── 路径 ──
    def ensure_dirs(self) -> None:
        self.frames_dir.mkdir(parents=True, exist_ok=True)

    def _jsonl_path(self) -> Path:
        return self.dir / f"{time.strftime('%Y%m%d')}.jsonl"

    # ── 写入 ──
    def append(self, record: dict[str, Any]) -> None:
        """追加一行 JSONL（同时维护内存中的最近记录环）。"""
        rec = dict(record)
        rec.setdefault("ts", _now_ts())
        rec.setdefault("session_id", self.session_id)
        self.ensure_dirs()
        with self._jsonl_path().open("a", encoding="utf-8") as fh:
            fh.write(json.dumps(rec, ensure_ascii=False, default=str) + "\n")
        if rec.get("type", "step") == "step":
            self._recent.append(rec)
            if len(self._recent) > 200:
                del self._recent[:-200]

    def session_start(self, *, purpose: str, target: str, mode: str, limits: dict[str, Any],
                      actions: list[str], spec_digest: str = "") -> None:
        self.append({"type": "session_start", "purpose": purpose, "target": target,
                     "mode": mode, "limits": limits, "actions": actions,
                     "spec_digest": spec_digest})
        self.summarize(force=True)

    def session_end(self, *, reason: str, released: list[str] | None = None,
                    detail: dict[str, Any] | None = None) -> None:
        self.append({"type": "session_end", "reason": reason,
                     "released": list(released or []), "detail": detail or {}})
        self.summarize(force=True)

    def note(self, kind: str, **fields: Any) -> None:
        """非步记录（升级/暂停/恢复/上限…）。"""
        self.append({"type": kind, **fields})

    # ── 汇总 ──
    def record_step(self, rec: dict[str, Any]) -> None:
        self._stats["steps"] += 1
        if rec.get("injected"):
            self._stats["injected"] += 1
        if rec.get("uncertain_reason"):
            self._stats["uncertain"] += 1
        if rec.get("uncertain_reason") == "frame_invalid":
            self._stats["frame_invalid"] += 1
        if rec.get("frame_changed") is False:
            self._stats["frame_changed_false"] += 1
        src = rec.get("state_source")
        if src in ("hook", "model"):
            self._stats["state_source"][src] += 1
        qid = rec.get("question_id") or "action"
        conf = rec.get("confidence")
        q = self._stats["questions"].get(qid)
        if q is None:
            if len(self._stats["questions"]) >= _MAX_QUESTION_KEYS:
                return
            q = self._stats["questions"][qid] = {"asked": 0, "conf_sum": 0.0, "with_conf": 0,
                                                 "uncertain": 0, "chosen": 0}
        q["asked"] += 1
        if isinstance(conf, (int, float)):
            q["conf_sum"] += float(conf)
            q["with_conf"] += 1
        if rec.get("uncertain_reason"):
            q["uncertain"] += 1
        else:
            q["chosen"] += 1

    def escalation(self, payload: dict[str, Any]) -> None:
        self._stats["escalations"] += 1
        self.ensure_dirs()
        path = self.dir / "last_escalation.json"
        path.write_text(json.dumps({**payload, "ts": _now_ts(), "session_id": self.session_id},
                                   ensure_ascii=False, indent=2, default=str), encoding="utf-8")

    def summarize(self, *, force: bool = False) -> dict[str, Any]:
        out = dict(self._stats)
        out["questions"] = {
            k: {"asked": v["asked"],
                "mean_confidence": round(v["conf_sum"] / v["with_conf"], 4) if v["with_conf"] else None,
                "uncertain": v["uncertain"], "chosen": v["chosen"]}
            for k, v in self._stats["questions"].items()
        }
        out["updated_at"] = _now_ts()
        if force or not (self.dir / "summary.json").exists():
            self.ensure_dirs()
            (self.dir / "summary.json").write_text(
                json.dumps(out, ensure_ascii=False, indent=2, default=str), encoding="utf-8")
        return out

    # ── 截图 ──
    def save_frame(self, frame: Any, step_id: int, stage: str) -> Optional[Path]:
        """保存一帧（升级/异常必存；常规每 snapshot_every 步一张由调用方决定）。"""
        if frame is None or getattr(frame, "image", None) is None:
            return None
        try:
            from PIL import Image
            img = frame.image
            if hasattr(img, "shape") and getattr(img, "ndim", 0) == 3:
                pil = Image.fromarray(img)
            else:
                pil = img
            self.ensure_dirs()
            path = self.frames_dir / f"{int(step_id):06d}-{stage}.jpg"
            pil.convert("RGB").save(path, format="JPEG", quality=80)
            self.last_frame = path
            return path
        except Exception:  # noqa: BLE001 - 截图留存失败不应影响控制流
            return None

    def cleanup(self) -> int:
        """按 keep_days / keep_images 删除最老的截图，返回删除数量。"""
        if not self.frames_dir.exists():
            return 0
        files = sorted((p for p in self.frames_dir.iterdir() if p.suffix.lower() == ".jpg"),
                       key=lambda p: p.stat().st_mtime)
        removed = 0
        if self.keep_days > 0:
            cutoff = _now_ts() - self.keep_days * 86400
            for p in list(files):
                try:
                    if p.stat().st_mtime < cutoff:
                        p.unlink()
                        files.remove(p)
                        removed += 1
                except OSError:
                    continue
        if self.keep_images > 0 and len(files) > self.keep_images:
            for p in files[: len(files) - self.keep_images]:
                try:
                    p.unlink()
                    removed += 1
                except OSError:
                    continue
        return removed

    # ── vfs 文本 ──
    def ops_text(self) -> str:
        lines = [f"# ui-ops/{self.module} 最近 {len(self._recent)} 步", ""]
        if not self._recent:
            lines.append("(本会话暂无步记录)")
        for r in self._recent:
            lines.append(
                f"[{int(r.get('step_id', 0)):>4}] {r.get('state') or '-'}({r.get('state_source') or '-'}) "
                f"→ {r.get('action') or '-'} conf={r.get('confidence')} "
                f"inj={r.get('injected')} {r.get('uncertain_reason') or ''}".rstrip())
        return "\n".join(lines)

    def summary_text(self) -> str:
        s = self.summarize()
        lines = [f"# ui-ops/{self.module} 会话汇总", "",
                 f"会话: {s['session_id']}  开始: "
                 f"{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(s['started_at']))}",
                 f"步数: {s['steps']}  已注入: {s['injected']}  不确定: {s['uncertain']}  "
                 f"升级: {s['escalations']}",
                 f"帧无效: {s['frame_invalid']}  注入后画面未变: {s['frame_changed_false']}",
                 f"状态来源: hook={s['state_source']['hook']} model={s['state_source']['model']}", ""]
        lines.append("## 每问统计")
        if not s["questions"]:
            lines.append("(无)")
        for qid, q in s["questions"].items():
            lines.append(f"- {qid}: 问 {q['asked']} 次，平均把握度 {q['mean_confidence']}，"
                         f"不确定 {q['uncertain']}，采纳 {q['chosen']}")
        if s["frame_changed_false"] >= 10 and s["injected"]:
            lines.append("")
            lines.append("⚠ 注入后画面长期未变化：可能是反作弊/raw input 丢弃了注入，"
                         "或游戏本身不响应这些键。")
        return "\n".join(lines)

    def last_escalation_text(self) -> str:
        path = self.dir / "last_escalation.json"
        if not path.exists():
            return "(尚无升级记录)"
        return path.read_text(encoding="utf-8", errors="replace")

    def latest_frame_path(self) -> Optional[Path]:
        if self.last_frame is not None and self.last_frame.exists():
            return self.last_frame
        if not self.frames_dir.exists():
            return None
        files = sorted(self.frames_dir.glob("*.jpg"), key=lambda p: p.stat().st_mtime)
        return files[-1] if files else None
