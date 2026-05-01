"""
桥接模块: 重定向到 faust_backend.tools 包。
原有消费者（main.py 等）无需修改。
"""
from faust_backend.tools import *  # noqa: F401, F403

# 显式重新导出 _registry 中的符号
from faust_backend.tools._registry import (  # noqa: F401
    toollist,
    ORIGINAL_TOOL_FUNCS,
    STARTED,
    DIARY_DIR,
    ARAYA_ALLOWED_TOOL_NAMES,
    DEFAULT_EXCLUDED_TOOL_NAMES,
    VRM_ONLY_TOOL_NAMES,
    get_tools_for_agent,
    refresh_runtime_paths,
    _tool_func_name,
    _run_async_in_thread,
    register,
)

# 需要供 skill.py 等子模块使用的函数
from faust_backend.tools.hil import HILRequest  # noqa: F401
from faust_backend.tools._patch_utils import (  # noqa: F401
    apply_patch_text,
    extract_section_chunks,
    safe_read_file_range,
)
