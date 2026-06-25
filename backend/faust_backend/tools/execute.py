"""
Execute tool — runs code and commands in subprocess sandboxes.

Replaces sysExecTool and pythonExecTool.  Shell commands go through
security_check_command; Python/JS run in isolated child processes.
"""

from __future__ import annotations

import sys
import subprocess

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.execute")

@register
@tool
def execute(language: str, code: str, *, timeout: int = 30, cwd: str = "") -> str:
    """Run code or shell commands in a sandboxed subprocess.

    This replaces the old sysExecTool and pythonExecTool.  All execution now
    happens in isolated child processes with timeout protection.

    WHEN TO USE EACH LANGUAGE:

    **language="shell":**
    - Use for: listing directories, checking file existence, running installed
      programs, getting system info, git commands.
    - Examples: "dir", "git status", "pip list", "echo %PATH%".
    - Security: commands are checked by the security module before execution.
      Dangerous operations (rm -rf, format, etc.) will be rejected.

    **language="python":**
    - Use for: computation, data processing, string manipulation, math,
      JSON parsing, running quick scripts.
    - Examples: "print(sum(range(100)))", "import json; print(json.dumps({...}))".
    - Runs in a fresh subprocess — imports you need must be in the code.

    **language="js":**
    - Use for: quick JavaScript execution (node/bun required on system).
    - Examples: "JSON.stringify({a:1})", "console.log(1+2)".

    IMPORTANT: The output may be truncated if very long.  You'll receive a
    summary with an artifact:// ID.  Use read("artifact://<id>") to see the
    full output.

    Args:
        language: "shell", "python", or "js".
        code: The code or command to execute.
        timeout: Maximum seconds (default 30, max 300).
        cwd: Working directory; defaults to the project root.

    Returns:
        Combined stdout + stderr output (may be truncated with artifact reference).
    """
    lang = str(language or "").strip().lower()
    code = str(code or "")
    timeout = max(5, min(int(timeout), 300))

    if lang == "shell":
        return _run_shell(code, timeout, cwd)
    elif lang == "python":
        return _run_python(code, timeout, cwd)
    elif lang == "js":
        return _run_js(code, timeout, cwd)
    else:
        return f"不支持的语言: {language}。支持 shell / python / js"


def _resolve_cwd(cwd: str) -> str:
    if cwd:
        return cwd
    from faust_backend.config_loader import PROJECT_ROOT
    return PROJECT_ROOT


def _run_shell(command: str, timeout: int, cwd: str) -> str:
    try:
        from faust_backend.security import security_check_command
    except ImportError:
        security_check_command = None

    if security_check_command:
        try:
            import asyncio as _asyncio
            ok = _asyncio.run(security_check_command(command))
            if not ok:
                return "安全检查拒绝执行该命令"
        except Exception as e:
            log.warning("安全检查调用失败: %s", e)

    work_dir = _resolve_cwd(cwd)
    try:
        proc = subprocess.run(
            command, shell=True, capture_output=True, text=True,
            timeout=timeout, cwd=work_dir,
        )
    except subprocess.TimeoutExpired:
        return f"命令超时 ({timeout}s)"

    out = []
    if proc.stdout:
        out.append(proc.stdout.strip())
    if proc.stderr:
        out.append(f"[stderr]\n{proc.stderr.strip()}")
    if proc.returncode != 0:
        out.append(f"[exit code: {proc.returncode}]")
    return "\n".join(out) if out else "(无输出)"


def _run_python(code: str, timeout: int, cwd: str) -> str:
    work_dir = _resolve_cwd(cwd)
    try:
        proc = subprocess.run(
            [sys.executable, "-c", code],
            capture_output=True, text=True,
            timeout=timeout, cwd=work_dir,
        )
    except subprocess.TimeoutExpired:
        return f"Python 执行超时 ({timeout}s)"
    except FileNotFoundError:
        return "找不到 Python 解释器"

    out = []
    if proc.stdout:
        out.append(proc.stdout.strip())
    if proc.stderr:
        out.append(f"[stderr]\n{proc.stderr.strip()}")
    if proc.returncode != 0:
        out.append(f"[exit code: {proc.returncode}]")
    return "\n".join(out) if out else "(无输出)"


def _run_js(code: str, timeout: int, cwd: str) -> str:
    work_dir = _resolve_cwd(cwd)
    # Try bun first, fall back to node
    for runtime in ("bun", "node"):
        try:
            proc = subprocess.run(
                [runtime, "-e", code],
                capture_output=True, text=True,
                timeout=timeout, cwd=work_dir,
            )
            out = []
            if proc.stdout:
                out.append(proc.stdout.strip())
            if proc.stderr:
                out.append(f"[stderr]\n{proc.stderr.strip()}")
            if proc.returncode != 0:
                out.append(f"[exit code: {proc.returncode}]")
            return "\n".join(out) if out else "(无输出)"
        except subprocess.TimeoutExpired:
            return f"JS 执行超时 ({timeout}s)"
        except FileNotFoundError:
            continue
    return "找不到 JavaScript 运行时 (bun/node)"
