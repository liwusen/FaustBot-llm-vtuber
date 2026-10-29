"""架构守卫：把「布局」这件事钉进 CI，防止后续改动悄悄退回旧形态。

三条硬规则 + 一条根目录白名单，全部用**静态文本/AST 分析**，不 import 应用
（跑一次 pytest 只花几毫秒）。

规则来源：`__dev__/backend-layout-plan.md` 的 Q2（硬断、零 shim）、Q5（`__file__`
收敛到唯一锚点）、§4.7（依赖方向）、§4.8（`download_vad.py` 唯一豁免）。
"""

from __future__ import annotations

import ast
import re
from pathlib import Path

BACKEND = Path(__file__).resolve().parents[1]
PKG = BACKEND / "faust_backend"

#: 业务域包：core/ 是叶子，不许碰到它们
DOMAIN_PACKAGES = (
    "llm",
    "agent",
    "live",
    "avatar",
    "integrations",
    "services",
    "plugins",
    "api",
    "app",
)

#: api/ 只做 HTTP/WS 适配，不许伸手进 Agent 运行时的**内部件**。
#: 注意 `agent.runtime.scheduler` 不在此列：它是「提交一个回合」的对外门面
#: （`AgentRequest` / `submit` / `TurnHandle`），`api/chat/chat.py` 正是它的调用方。
RUNTIME_INTERNALS = (
    "faust_backend.agent.runtime.middleware",
    "faust_backend.agent.runtime.lock",
    "faust_backend.agent.runtime.compact",
)

#: vendored 第三方代码不受本仓规则约束
VENDORED = ("vendor", "blivedm")


def _python_files(root: Path):
    for path in sorted(root.rglob("*.py")):
        if "__pycache__" in path.parts:
            continue
        if VENDORED and any(part in VENDORED for part in path.parts):
            continue
        yield path


def _toplevel_imports(path: Path) -> list[str]:
    """只返回**模块顶层**的 import 目标（函数内的延迟导入不算结构依赖）。"""
    tree = ast.parse(path.read_text(encoding="utf-8"))
    found: list[str] = []
    for node in tree.body:
        if isinstance(node, ast.Import):
            found.extend(alias.name for alias in node.names)
        elif isinstance(node, ast.ImportFrom) and node.level == 0 and node.module:
            found.append(node.module)
            # from x import a, b —— a/b 可能是子模块，也可能是符号；两种都要能匹配
            found.extend(f"{node.module}.{alias.name}" for alias in node.names)
    return found


def test_core_does_not_import_domain_packages():
    """core/ 是叶子层：模块级 import 不许指向任何业务域包。

    函数内的延迟导入（例如 security.py 在审批流程里才取 runtime state）是允许的——
    这里守的是**结构依赖**，不是运行时调用。
    """
    offenders = []
    for path in _python_files(PKG / "core"):
        for name in _toplevel_imports(path):
            for domain in DOMAIN_PACKAGES:
                if name == f"faust_backend.{domain}" or name.startswith(f"faust_backend.{domain}."):
                    offenders.append(f"{path.relative_to(PKG).as_posix()} -> {name}")
    assert not offenders, "core/ 不许依赖业务域包：\n" + "\n".join(offenders)


def test_api_does_not_import_agent_runtime_internals():
    """api/ 只做适配：不许 import Agent 运行时的内部件（state / rebuild 等允许）。"""
    offenders = []
    for path in _python_files(PKG / "api"):
        for name in _toplevel_imports(path):
            if any(name == bad or name.startswith(bad + ".") for bad in RUNTIME_INTERNALS):
                offenders.append(f"{path.relative_to(PKG).as_posix()} -> {name}")
    assert not offenders, (
        "api/ 不许 import Agent 运行时内部件（middleware/lock/compact/scheduler）：\n"
        + "\n".join(offenders)
    )


def test_file_derivations_are_single_anchor():
    """`__file__` 深度推算只允许出现在唯一锚点（+ 登记过的例外）。

    允许清单（改动这里之前先读 `__dev__/backend-layout-plan.md` §4.8）：
    - `core/config.py`：**唯一**锚点，导出 BACKEND_ROOT/PACKAGE_ROOT/REPO_ROOT，
      搬它时只改两行 d_name 层数；
    - `download_vad.py`：被 CI 按脚本路径调用、又跑在只会注入 script_dir 的
      内嵌解释器里，`parents[1]` 在当前位置恰好正确 → **明确豁免搬运**；
    - `processors/worker.py`：脚本自举，在**任何 import 之前**给 sys.path 补
      backend/，此时还没有 conf 可用；
    - `memory/tools.py`、`processors/manager.py`、`processors/registry.py`：
      只取**同目录**文件（`parent / "x"`、`with_name()`、`getattr(module, "__file__")`），
      不推算包根。
    """
    expected = {
        "core/config.py",
        "download_vad.py",
        "memory/tools.py",
        "processors/manager.py",
        "processors/registry.py",
        "processors/worker.py",
    }
    actual = {
        path.relative_to(PKG).as_posix()
        for path in _python_files(PKG)
        if re.search(r"^\s*[^#\n]*__file__", path.read_text(encoding="utf-8"), re.MULTILINE)
    }
    assert actual == expected, (
        "`__file__` 使用点变了。新增/删除都要同步这份清单与计划 §4.8：\n"
        f"  多出来：{sorted(actual - expected)}\n"
        f"  少掉了：{sorted(expected - actual)}"
    )

    # 真正危险的只有「向上数层数」这一种写法
    depth_ok = {"core/config.py", "download_vad.py", "processors/worker.py"}
    depth_pattern = re.compile(r"parents\[\d+\]|d_name\(d_name|dirname\(dirname")
    offenders = [
        path.relative_to(PKG).as_posix()
        for path in _python_files(PKG)
        if depth_pattern.search(path.read_text(encoding="utf-8"))
        and path.relative_to(PKG).as_posix() not in depth_ok
    ]
    assert not offenders, "`__file__` 深度推算出现在未登记的模块里：\n" + "\n".join(offenders)


def test_root_modules_are_only_the_registered_exception():
    """`faust_backend/` 根目录只许留 `download_vad.py`（其余都在领域包里）。

    要新增根级模块时，先问一句「它属于哪个域」——答案几乎总是某个子包。
    """
    root_modules = sorted(p.name for p in PKG.glob("*.py"))
    assert root_modules == ["download_vad.py"], (
        "faust_backend/ 根目录只允许 download_vad.py（§4.8 豁免），实际："
        f"{root_modules}"
    )
