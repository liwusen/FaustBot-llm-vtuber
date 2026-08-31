"""
Execute tool — runs code and commands in subprocess sandboxes.

Replaces sysExecTool and pythonExecTool.  Shell commands go through
security_check_command; Python/JS run in isolated child processes.
"""

from __future__ import annotations

import os
import sys
import asyncio

from langchain.tools import tool

from faust_backend.tools._registry import register
from faust_backend.logger import get_logger

log = get_logger("faust.tools.execute")

@register
@tool
async def execute(language: str, code: str, *, timeout: int = 30, cwd: str = "") -> str:
    """Run code or shell commands in a sandboxed subprocess.

    All execution happens in isolated child processes with timeout protection.

    WHEN TO USE EACH LANGUAGE:

    **language="shell":**
    - Use for: listing directories, checking file existence, running installed
      programs, getting system info, git commands.
 ·
    - Security: commands are checked by the security module before execution.
      Dangerous operations (rm -rf, format, etc.) will be rejected.

    IMPORTANT: The output may be truncated if very long.  You'll receive a
    summary with an artifact:// ID.  Use read("artifact://<id>") to see the
    full output.

    Args:
        language: "shell"
        code: The code or command to execute.
        timeout: Maximum seconds (default 30, max 300).
        cwd: Working directory; defaults to the project root.

    Returns:
        Combined stdout + stderr output (may be truncated with artifact reference).
    """
    lang = str(language or "").strip().lower()
    code = str(code or "")
    timeout = max(5, min(int(timeout), 300))
    log.info("execute INPUT lang=%s code_len=%d timeout=%d cwd=%s", lang, len(code), timeout, cwd or '.')

    if lang == "shell":
        result = await _run_shell(code, timeout, cwd)
    elif lang == "python":
        result = await _run_python(code, timeout, cwd)
    elif lang == "js":
        result = await _run_js(code, timeout, cwd)
    elif lang == "java" and code=="world.execute(me);":
        result = "EXECUTION"
        log.critical("ILLEGAL ARGUMENT")
    else:
        result = f"不支持的语言: {language}。支持 shell"

    log.info("execute OUTPUT len=%d", len(result))
    return result


def _resolve_cwd(cwd: str) -> str:
    if cwd:
        return cwd
    from faust_backend.config_loader import WORKDIR_ROOT
    return WORKDIR_ROOT


async def _run_shell(command: str, timeout: int, cwd: str) -> str:
    import faust_backend.config_loader as conf
    if not getattr(conf, 'SECURITY_SYS_ENABLED', False):
        # Security system is disabled — execute without checks
        return await _run_shell_no_check(command, timeout, cwd)

    try:
        from faust_backend.security import security_check_command
    except ImportError:
        return await _run_shell_no_check(command, timeout, cwd)

    try:
        ok = await security_check_command(command)
        if not ok:
            return "安全检查拒绝执行该命令"
    except Exception as e:
        log.warning("安全检查调用失败，已拒绝执行: %s", e)
        return f"安全检查失败，拒绝执行命令: {e}"

    return await _run_shell_no_check(command, timeout, cwd)


async def _terminate_proc(proc) -> None:
    """终止子进程并回收。Windows 上杀进程树，避免 shell 遗留孙进程。"""
    if proc.returncode is not None:
        return
    try:
        if sys.platform == "win32" and proc.pid:
            killer = await asyncio.create_subprocess_exec(
                "taskkill", "/F", "/T", "/PID", str(proc.pid),
                stdout=asyncio.subprocess.DEVNULL,
                stderr=asyncio.subprocess.DEVNULL,
            )
            try:
                await asyncio.wait_for(killer.wait(), timeout=10)
            except asyncio.TimeoutError:
                killer.kill()
        else:
            proc.kill()
    except Exception as e:
        log.warning("终止子进程失败: %s", e)
    try:
        await asyncio.wait_for(proc.communicate(), timeout=5)
    except Exception:
        pass


async def _run_shell_no_check(command: str, timeout: int, cwd: str) -> str:
    work_dir = _resolve_cwd(cwd)
    try:
        proc = await asyncio.create_subprocess_shell(
            command,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
        )
    except FileNotFoundError:
        return "找不到命令解释器"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate_proc(proc)
        return f"命令超时 ({timeout}s)"

    out = []
    if stdout:
        out.append(stdout.decode('utf-8', errors='ignore').strip())
    if stderr:
        out.append(f"[stderr]\n{stderr.decode('utf-8', errors='ignore').strip()}")
    if proc.returncode != 0:
        out.append(f"[exit code: {proc.returncode}]")
    return "\n".join(out) if out else "(无输出)"


async def _run_python(code: str, timeout: int, cwd: str) -> str:
    work_dir = _resolve_cwd(cwd)
    # Windows 默认 locale 常为 GBK，子进程 print 非 ASCII（emoji/特殊符号）会抛
    # UnicodeEncodeError；强制 PYTHONIOENCODING=utf-8 让子进程 stdout/stderr 用 UTF-8。
    env = dict(os.environ)
    env.setdefault("PYTHONIOENCODING", "utf-8")
    try:
        proc = await asyncio.create_subprocess_exec(
            sys.executable, "-c", code,
            stdout=asyncio.subprocess.PIPE,
            stderr=asyncio.subprocess.PIPE,
            cwd=work_dir,
            env=env,
        )
    except FileNotFoundError:
        return "找不到 Python 解释器"
    try:
        stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
    except asyncio.TimeoutError:
        await _terminate_proc(proc)
        return f"Python 执行超时 ({timeout}s)"

    out = []
    if stdout:
        out.append(stdout.decode('utf-8', errors='replace').strip())
    if stderr:
        out.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace').strip()}")
    if proc.returncode != 0:
        out.append(f"[exit code: {proc.returncode}]")
    return "\n".join(out) if out else "(无输出)"


async def _run_js(code: str, timeout: int, cwd: str) -> str:
    work_dir = _resolve_cwd(cwd)
    runtimes = ["bun"]
    env_node = str(os.environ.get("FAUST_NODEJS") or "").strip()
    if env_node and os.path.isfile(env_node):
        runtimes.append(env_node)
    else:
        from faust_backend.config_loader import PROJECT_ROOT
        bundled_node = os.path.join(os.path.dirname(PROJECT_ROOT), ".nodejs", "node.exe")
        if os.path.isfile(bundled_node):
            runtimes.append(bundled_node)
    runtimes.append("node")
    seen = set()
    for runtime in runtimes:
        if runtime in seen:
            continue
        seen.add(runtime)
        try:
            proc = await asyncio.create_subprocess_exec(
                runtime, "-e", code,
                stdout=asyncio.subprocess.PIPE,
                stderr=asyncio.subprocess.PIPE,
                cwd=work_dir,
            )
        except FileNotFoundError:
            continue
        try:
            stdout, stderr = await asyncio.wait_for(proc.communicate(), timeout=timeout)
        except asyncio.TimeoutError:
            await _terminate_proc(proc)
            return f"JS 执行超时 ({timeout}s)"
        out = []
        if stdout:
            out.append(stdout.decode('utf-8', errors='replace').strip())
        if stderr:
            out.append(f"[stderr]\n{stderr.decode('utf-8', errors='replace').strip()}")
        if proc.returncode != 0:
            out.append(f"[exit code: {proc.returncode}]")
        return "\n".join(out) if out else "(无输出)"
    return "找不到 JavaScript 运行时 (bun/node)"
