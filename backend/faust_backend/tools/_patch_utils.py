import re
import json
import tempfile
import zipfile
import shutil
import datetime
from pathlib import Path

import requests
import faust_backend.config_loader as conf


def safe_read_file_range(file_path: str, start_line: int, end_line: int) -> str:
    p = Path(file_path)
    if not p.exists() or not p.is_file():
        return f"文件不存在: {file_path}"
    if start_line < 1:
        start_line = 1
    with p.open("r", encoding="utf-8") as f:
        lines = f.readlines()
    total = len(lines)
    if total == 0:
        return f"File: `{file_path}`. Empty file."
    if end_line <= 0 or end_line > total:
        end_line = total
    if start_line > end_line:
        return f"无效行范围: start_line={start_line}, end_line={end_line}"
    body = "".join(lines[start_line - 1:end_line])
    return f"File: `{file_path}`. Lines {start_line} to {end_line} ({total} lines total):\n{body}"


def extract_section_chunks(patch_text: str) -> list[tuple[str, str, list[str]]]:
    lines = patch_text.splitlines()
    if not lines:
        raise ValueError("Patch 为空")
    if lines[0].strip() != "*** Begin Patch" or lines[-1].strip() != "*** End Patch":
        raise ValueError("Patch 必须以 *** Begin Patch 开始并以 *** End Patch 结束")

    body = lines[1:-1]
    chunks: list[tuple[str, str, list[str]]] = []
    current_action = None
    current_path = None
    current_lines: list[str] = []

    header_re = re.compile(r"^\*\*\*\s+(Add|Update|Delete)\s+File:\s+(.+?)\s*$")
    for line in body:
        match = header_re.match(line)
        if match:
            if current_action and current_path is not None:
                chunks.append((current_action, current_path, current_lines))
            current_action = match.group(1)
            current_path = match.group(2).strip()
            current_lines = []
        else:
            current_lines.append(line)

    if current_action and current_path is not None:
        chunks.append((current_action, current_path, current_lines))

    if not chunks:
        raise ValueError("Patch 中未找到任何文件操作段")
    return chunks


def apply_update_hunks(original: str, section_lines: list[str]) -> str:
    i = 0
    content = original
    while i < len(section_lines):
        line = section_lines[i]
        if line.startswith("@@"):
            i += 1
            old_lines: list[str] = []
            new_lines: list[str] = []
            while i < len(section_lines) and not section_lines[i].startswith("@@"):
                row = section_lines[i]
                if row.startswith("-"):
                    old_lines.append(row[1:])
                elif row.startswith("+"):
                    new_lines.append(row[1:])
                i += 1

            old_chunk = "\n".join(old_lines)
            new_chunk = "\n".join(new_lines)
            if old_chunk:
                if old_chunk not in content:
                    raise ValueError(f"更新失败，未在文件中找到旧代码块:\n{old_chunk[:200]}")
                content = content.replace(old_chunk, new_chunk, 1)
            elif new_chunk:
                if content and not content.endswith("\n"):
                    content += "\n"
                content += new_chunk
        else:
            i += 1
    return content


def apply_patch_text(patch_text: str) -> str:
    chunks = extract_section_chunks(patch_text)
    changed: list[str] = []
    for action, target_path, section_lines in chunks:
        p = Path(target_path)
        if action == "Add":
            p.parent.mkdir(parents=True, exist_ok=True)
            add_lines = [row[1:] if row.startswith("+") else row for row in section_lines]
            text = "\n".join(add_lines)
            if text and not text.endswith("\n"):
                text += "\n"
            p.write_text(text, encoding="utf-8")
            changed.append(f"Add {target_path}")
        elif action == "Delete":
            if p.exists():
                p.unlink()
            changed.append(f"Delete {target_path}")
        elif action == "Update":
            if not p.exists() or not p.is_file():
                raise ValueError(f"Update 失败，文件不存在: {target_path}")
            old = p.read_text(encoding="utf-8")
            new = apply_update_hunks(old, section_lines)
            p.write_text(new, encoding="utf-8")
            changed.append(f"Update {target_path}")
        else:
            raise ValueError(f"不支持的 patch 动作: {action}")

    return "Patch 应用成功:\n" + "\n".join(changed)


def find_skill_root(extract_dir: Path) -> Path:
    candidates = [p.parent for p in extract_dir.rglob("_meta.json") if p.is_file()]
    if not candidates:
        raise ValueError("skill 包中未找到 _meta.json")
    if len(candidates) == 1:
        return candidates[0]
    for c in candidates:
        if (c / "SKILL.md").exists():
            return c
    return candidates[0]


def install_skill_from_slug(slug: str, overwrite: bool = False) -> dict:
    api = f"https://wry-manatee-359.convex.site/api/v1/download?slug={requests.utils.quote(slug, safe='')}"
    with tempfile.TemporaryDirectory(prefix="faust-skill-") as td:
        td_path = Path(td)
        zip_path = td_path / "skill.zip"
        extract_dir = td_path / "extract"
        extract_dir.mkdir(parents=True, exist_ok=True)

        resp = requests.get(api, timeout=60)
        resp.raise_for_status()
        zip_path.write_bytes(resp.content)

        try:
            with zipfile.ZipFile(zip_path, "r") as zf:
                zf.extractall(extract_dir)
        except zipfile.BadZipFile as exc:
            raise ValueError("下载结果不是有效 ZIP") from exc

        skill_root = find_skill_root(extract_dir)
        meta_file = skill_root / "_meta.json"
        skill_doc = skill_root / "SKILL.md"
        if not skill_doc.exists():
            raise ValueError("skill 包缺少 SKILL.md")

        meta = json.loads(meta_file.read_text(encoding="utf-8"))
        skill_slug = str(meta.get("slug") or slug).strip()
        version = str(meta.get("version") or "0.0.0").strip()
        if not skill_slug:
            raise ValueError("skill slug 为空")

        agent_name = str(conf.AGENT_NAME)
        skill_dir = Path("agents") / agent_name / "skill.d"
        skill_dir.mkdir(parents=True, exist_ok=True)
        target_dir = skill_dir / skill_slug

        if target_dir.exists() and not overwrite:
            raise ValueError(f"skill 已存在: {skill_slug}，如需覆盖请设置 overwrite=true")
        if target_dir.exists():
            shutil.rmtree(target_dir)
        shutil.copytree(skill_root, target_dir)

        index_file = skill_dir / "skills.state.json"
        state = {"skills": {}}
        if index_file.exists():
            try:
                state = json.loads(index_file.read_text(encoding="utf-8"))
                if not isinstance(state, dict):
                    state = {"skills": {}}
            except Exception:
                state = {"skills": {}}

        skills = state.setdefault("skills", {})
        skills[skill_slug] = {
            "slug": skill_slug,
            "version": version,
            "installed_at": datetime.datetime.now().isoformat(),
            "enabled": True,
            "source": api,
            "path": str(target_dir.resolve()),
        }
        index_file.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")

        return {
            "slug": skill_slug,
            "version": version,
            "agent": agent_name,
            "path": str(target_dir.resolve()),
            "source": api,
        }

def _parse_patch(patch_text: str) -> list[dict]:
    """Parse patch instructions into a list of operations."""
    ops = []
    current = None
    current_body: list[str] = []

    lineno = 0
    for raw_line in patch_text.split("\n"):
        lineno += 1
        line = raw_line.strip()
        if not line:
            if current is not None:
                # Flush on blank line — DEL ops have no body, still valid
                current["body"] = current_body
                ops.append(current)
                current = None
                current_body = []
            continue

        if current is not None:
            # Body line
            if line.startswith("+"):
                current_body.append(line[1:])
            elif not line.startswith("+"):
                # Next operation header → flush current, treat as new header
                current["body"] = current_body
                ops.append(current)
                current = None
                current_body = []

        if current is None:
            # New operation header
            if " " in line:
                header, rest = line.split(" ", 1)
            else:
                header = line
                rest = ""
            header = header.upper().rstrip(":")
            rest = rest.rstrip(":")

            if header == "SWAP":
                start, end = _parse_range(rest)
                current = {"kind": "SWAP", "start": start, "end": end, "offset": start}
            elif header == "DEL":
                start, end = _parse_range(rest)
                current = {"kind": "DEL", "start": start, "end": end, "offset": start}
            elif header in ("INS.PRE", "INS.POST"):
                pos = int(rest) if rest else 1
                kind = "INS_PRE" if header == "INS.PRE" else "INS_POST"
                current = {"kind": kind, "start": pos, "end": pos, "offset": pos}
            else:
                raise ValueError(f"第{lineno}行: 未知指令 \"{header}\"，支持: SWAP, DEL, INS.PRE, INS.POST")

    # Flush any remaining operation (handles single-op or last-op without trailing blank line)
    if current is not None:
        current["body"] = current_body
        ops.append(current)

    if not ops:
        raise ValueError("Patch 不包含任何操作")
    return ops


def _parse_range(text: str) -> tuple[int, int]:
    """Parse 'N.=M' into (start_inclusive, end_inclusive)."""
    text = text.strip().rstrip(":")
    if ".=" in text:
        start, end = text.split(".=", 1)
        s = int(start)
        e = int(end)
        return (s, e)
    else:
        n = int(text)
        return (n, n)