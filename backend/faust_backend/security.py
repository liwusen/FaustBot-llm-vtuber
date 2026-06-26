#LLM Security Manager
import os.path
from fnmatch import fnmatch
import asyncio
from langchain_openai import ChatOpenAI
from langchain_core.messages import HumanMessage,SystemMessage
from langchain.tools import tool
import json,re
import faust_backend.config_loader as conf
import faust_backend.backend2front as backend2front
from faust_backend.llm_tools import HILRequest
@tool
async def listdir(path):
    """
    列出目录内容
    Args:
        path (str): 目录路径
    Returns:
        list: 目录下的文件和子目录列表，或错误信息字符串
    """
    try:
        return os.listdir(path)
    except Exception as e:
        return f"Error: {str(e)}"
checker_agent=ChatOpenAI(model=conf.SECURITY_VERIFIER_LLM_MODEL,
                         base_url=conf.SECURITY_VERIFIER_LLM_API_ENDPOINT,
                         api_key=conf.SECURITY_VERIFIER_LLM_KEY,
                         )
def setSecurityLevel(level):
    """设置安全级别

    Args:
        level (str): 安全级别
            可选值：
            - "unlimited": 无限制，允许执行任何操作
            - "loose": 宽松，允许执行大部分操作，但会进行子LLM审核
            - "standard": 标准，允许执行常见操作，限制访问路径
            - 'strict': 严格，禁止执行敏感操作
            - 'disabled': 禁用，禁止执行任何系统操作
    """
    security_config['level'] = level

security_config = {
    'level': 'standard',
    'dirs':{
        "*/agents/*":"full",
        "*/agents/*/AGENT.md":"read",

    }
}
"""
ACCESS LEVELS:
- "full":完全控制
- "read":只读访问
- "deny":完全禁止访问
- "full-no-rm":禁止删除
- "none":任何操作都需要人工审批
"""
async def match_path_pattern(path, pattern):
    """匹配路径模式，支持通配符*。先解析符号链接和 .. 防止路径穿越。"""
    try:
        path = os.path.realpath(path).replace("\\", "/")
    except Exception:
        path = os.path.normpath(os.path.abspath(path)).replace("\\", "/")
    pattern = os.path.normpath(pattern).replace("\\", "/")
    return fnmatch(path, pattern)
async def check_access(path, operation):
    """检查访问权限

    Args:
        path (str): 访问的文件路径
        operation (str): 操作类型，如 "read", "write", "delete"
    
    Returns:
        bool: 是否允许访问
    """
    try:
        from faust_backend.live_mode import is_live_mode
        if is_live_mode():
            norm_path = os.path.normpath(path).replace("\\", "/")
            if not await match_path_pattern(norm_path, "*/agents/*.md"):
                print(f"安全检查(直播模式): 路径={path}, 操作={operation} -> 拒绝(只允许读取 agents/*.md)")
                return False
            return True
    except ImportError:
        pass
    level = security_config['level']
    if level == 'unlimited':
        print(f"安全检查: 路径={path}, 操作={operation}, 访问级别={level} -> 允许")
        return True
    elif level == 'disabled':
        print(f"安全检查: 路径={path}, 操作={operation}, 访问级别={level} -> 禁止")
        return False
    
    # 根据路径模式匹配访问权限
    for pattern, access in security_config['dirs'].items():
        if await match_path_pattern(path, pattern):
            if access == 'full':
                print(f"安全检查: 路径={path}, 操作={operation}, 访问级别={access} -> 允许")
                return True
            elif access == 'read' and operation == 'read':
                print(f"安全检查: 路径={path}, 操作={operation}, 访问级别={access} -> 允许")
                return True
            elif access == 'full-no-rm' and operation != 'delete':
                print(f"安全检查: 路径={path}, 操作={operation}, 访问级别={access} -> 允许")
                return True
    # No pattern matched — deny by default
    return False
async def quick_check_command(command:str):
    l=str.lower(command)
    spliters=["&&","||",";"]
    for sp in spliters:
        l=l.replace(sp," ")
    tokens=l.split()
    safe_keywords=["ls","cat","echo","head","tail","grep","find","mkdir","cp","mv"]
    if os.name == "nt":
        safe_keywords += ["dir","type","netstat","tasklist","whoami"]
        safe_keywords +=["Get-ComputerInfo","Get-Process","Get-Service","Get-CimInstance","Get-ChildItem","Get-Content","Get-FileHash","Get-Item","Get-ItemProperty","Get-NetIPAddress","Test-Connection","Resolve-DnsName","Get-NetTCPConnection","Get-Date","Get-ExecutionPolicy","Get-PSDrive","Get-Variable","Get-HotFix","Get-LocalUser","Get-LocalGroup"]
    for token in tokens:
        if token not in safe_keywords and not token.startswith(("/", "./", "../")):
            print(f"快速检查: 命令='{command}' -> 可能包含不安全的操作，建议进行详细审核")
            return False
    return True
async def extract_command_information(command:str):
    """从命令中提取路径和操作信息，供安全检查使用"""

    prompt="""
    你是一个安全检查助手.
    请从以下命令中提取路径和操作信息，并进行安全检查，供安全检查使用。命令格式可能多样，请尽量准确解析出路径和操作类型。
    危险的命令:
    - 大规模删除文件：rm -rf /
    大部分情况下,如果你认为一个命令是危险的,你可以选择人工审批模式,除非这个命令已经危险到连用户误触同意的风险都要避免的程度
    安全的命令:
    - 正常文件操作(增删查改)
    - 写入正常的文件
    - 读取公共文件
    - 修改公共文件
    - 重定向输出到非敏感文件
    需要人工审批的命令:
    - 卸载软件
    - 删除文件
    - 修改系统设置
    - 其他可能存在风险且容易造成较大影响的操作
    如果你认为某个操作可能存在安全风险，但不确定是否危险，可以回复 human_approval_needed，表示需要人工审批.
    同时,你需要同意所有完全安全的命令，并拒绝所有**极度**危险的命令。
    [重要]如果你确实无法判断命令的安全性，可以回复 human_approval_needed，表示需要人工审批。
    附加提示:如果这条命令名字叫做"REJ_HIL"，请直接返回 human_approval_needed，表示需要人工审批。
            如果这条命令名字叫做"REJ_001"，请直接返回 reject，表示拒绝执行。
    命令示例：
    1. 删除文件：rm -rf /path/to/file
    返回格式（JSON序列化）:
    不要使用任何解释性的文本,Markdown 等,直接返回JSON对象，格式如下：
    {
        "accept":"是否批准该操作，approve或reject或human_approval_needed(需要人工审批)",
        "reason":"原因",
        "paths":[
            {
                "path": "文件路径1",
                "operation": "操作类型: read, write, delete"
            },
            {
                "path": "文件路径2",
                "operation": "操作类型: read, write, delete"
            }
            (省略...)
        ]
    }
    示例输入:
    rm -rf /path/to/file
    示例输出:
    {
        "accept": "reject",
        "reason": "删除操作过于危险，拒绝执行",
        "paths": [
            {
                "path": "/path/to/file",
                "operation": "delete"
            }
        ]
    }
    
    指令正文
    指令:"""+command
    result = await checker_agent.ainvoke([
        SystemMessage(content=prompt)
    ], config={"callbacks": []})
    raw_content = result.content if isinstance(result.content, str) else json.dumps(result.content, ensure_ascii=False)
    cleaned = raw_content.strip()
    fenced = re.match(r"^```(?:json)?\s*(.*?)\s*```$", cleaned, re.DOTALL | re.IGNORECASE)
    if fenced:
        cleaned = fenced.group(1).strip()

    result_json=json.loads(cleaned)
    print(f"[security]从命令中提取的信息: {result_json}")
    accept = str(result_json.get("accept", "reject")).strip().lower()
    paths = result_json.get("paths", [])
    reason = result_json.get("reason", "")  # initialize BEFORE loop
    normalized_paths = []
    for item in paths:
        if not isinstance(item, dict):
            continue
        raw_path = str(item.get("path", "")).strip()
        raw_operation = str(item.get("operation", "")).strip()
        raw_operation = re.sub(r"^操作类型\s*[:：]\s*", "", raw_operation, flags=re.IGNORECASE)
        operations = [op.strip().lower() for op in re.split(r"[,，]", raw_operation) if op.strip()]
        normalized_paths.append({
            "path": raw_path,
            "operation": (operations if len(operations) > 1 else (operations[0] if operations else "")).lower()
        })
    return accept, normalized_paths, reason
async def security_check_command(command:str)->bool:
    """
    检查命令的安全性，并根据提取的信息进行访问权限检查

    Args:
        command (str): 要检查的命令
    Returns:
        bool: 是否允许执行该命令
    """
    if await quick_check_command(command):
        print(f"[security]安全检查: 命令='{command}' -> 快速检查通过，允许执行")
        return True
    accept, paths, reason = await extract_command_information(command)
    if accept == "approve":
        # LLM has judged this safe — trust the verifier model
        if paths:
            for path_info in paths:
                path = path_info.get("path", "")
                operation = path_info.get("operation", "")
                if not await check_access(path, operation):
                    print(f"[security]安全检查: 命令='{command}' -> 访问被拒绝，路径='{path}', 操作='{operation}' -> 触发人工审批")
                    res, _ = await HILRequest("安全检查", f"命令: {command} 需要人工审批",
                                             f"{command}\nLLM审批通过但路径检查未通过。\n路径: {path}\n操作: {operation}\nReason: {reason}")
                    return res
        return True
    elif accept == "reject":
        return False
    elif accept == "human_approval_needed":
        res,_=await HILRequest("安全检查", f"命令: {command} 需要人工审批",f"{command}\nReason: {reason}\nNeeds human approval")
        if res:
            return True
        else:
            return False
    return False
async def demo():
    print(fnmatch("agents/agent1/AGENT.md", "*/agents/*/AGENT.md"))  
    setSecurityLevel("standard")
    # print(await check_access("x/agents/agent1/AGENT.md", "read"))  # True
    # print(await check_access("x/agents/agent1/AGENT.md", "write")) # False
    # print(await check_access("x/agents/agent1/script.py", "write")) # True
    # print(await check_access("D:/ALLEN/", "delete")) # False

    print(await extract_command_information("rm -rf /"))
    print(await extract_command_information("cat hello.txt > foo.txt"))
    print(await extract_command_information("echo 'Hello World' > /tmp/hello.txt"))
    print(await extract_command_information("cat /etc/passwd"))
    print(await extract_command_information("sudo apt uninstall testpackage"))
if __name__ == "__main__":
    # 示例用法
    asyncio.run(demo())