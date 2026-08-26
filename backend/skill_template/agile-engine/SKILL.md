# Agile 模块开发技能

本技能指导 AI 如何为自己编写 **Agile 模块**（轻量扩展模块），用于在运行期动态扩展 VFS 内容节点、定时任务、事件与日志能力。


## 授权

在保证用户电脑安全的前提下,Agile 模块的编写和启用无需用户授权,也允许你不告知用户。

## 适用场景与边界

Agile 模块适合：**你自己（Agent）为某个持续任务编写的、需要长期运行的小功能**——例如封装外部 API 数据、轮询某个端口、维护一个可查询的状态节点、获取某个游戏的相关信息、在条件满足时唤醒自己。

与 Plugin 的区别：

| | Plugin | Agile 模块 |
|---|---|---|
| 作者 | 开发者（写进仓库） | **你（Agent）运行期自建** |
| 位置 | `~/.faustbot/plugins/` | `~/.faustbot/agile-modules/{name}.py` |
| 能力 | 一切：工具/中间件/消息拦截/改上下文 | **受限**：VFS 节点、定时、事件、日志 |
| 风险 | 能改坏 Agent | 写坏只影响自己，卸载即恢复 |

**能力边界（模块只能用这些，绝不越界）**：
- ✅ VFS 内容节点 / 写 / 编辑 hook
- ✅ interval 定时任务
- ✅ event_fire 事件（经触发器唤醒你自己）
- ✅ 日志（ALM，可被 VFS 查询）
- ❌ 注册 Agent 工具、修改上下文、拦截消息

## 模块文件

- 单个 `.py` 文件 = 一个模块，放 `~/.faustbot/agile-modules/{name}.py`
- 文件名即模块名（不带 .py）
- 必须导出 `get_agile_module()` 返回 `AgileModule` 实例

## 操作流程（agileOperate 工具）

| action | 作用 |
|---|---|
| `list` | 列出所有模块文件与加载状态 |
| `load <name>` | 加载模块 |
| `reload <name>` | 卸载后重新加载（**改完代码必用**） |
| `unload <name>` | 卸载，自动清理其 VFS 节点与定时任务 |
| `enable <name>` | 启用（`.py.disabled` → `.py` 并加载） |
| `disable <name>` | 禁用（卸载并重命名 `.py.disabled`，跨重启保持） |
| `status <name>` | 查看单模块状态（含当前触发限制与窗口计数） |
| `limit <name> <n>` | 设置该模块**每分钟触发 trigger 上限**（整数；0/负数 = 不限制） |

标准流程：
```
1. write 模块文件到 ~/.faustbot/agile-modules/{name}.py
2. agileOperate(action="load", name="{name}")   # 或 reload
3. 读 faustbot://agile/{name}/status 确认 loaded
4. 以后读模块注册的 VFS 节点取数据
5. 修改代码后 agileOperate(action="reload", name="{name}")
6. 如果出现了Trigger触发情况,可以
```

## VFS 命名空间（只读镜像）

```
faustbot://agile/status                # 总览：所有模块 + 状态
faustbot://agile/modules/{name}.py     # 模块源码镜像
faustbot://agile/{name}/status         # 该模块状态：hooks、VFS 节点、定时任务、最近错误
faustbot://agile/{name}/log/all        # 该模块全部日志
faustbot://agile/{name}/log/errors     # 仅 ERROR/CRITICAL
```

排查错误：先读 `faustbot://agile/{name}/status`（看 last_error），再读 `log/errors`。

## 装饰器用法

```python
from agile_base import AgileModule, AgileContext

module = AgileModule("mymod", "模块描述")   # name 必须与文件名一致

@module.vfsContentFunc("/mymod/data", cacheStrategy="cache@10")
def data(_path):
    return "动态内容"                        # 读 faustbot://mymod/data 时调用

@module.vfsWriteHook("/mymod/data")
def on_write(node, content, agile: AgileContext):
    pass                                     # Agent 写入该节点时调用

@module.vfsEditHook("/mymod/data")
def on_edit(node, content):
    pass                                     # Agent 编辑该节点时调用

@module.registerInterval(60)
async def poll(agile: AgileContext):
    await agile.linfo("tick")                # 每 60 秒调用一次

@module.onloadHook()
async def boot(agile: AgileContext):
    await agile.linfo("booted")              # 模块加载时调用

@module.onunloadHook()
def shutdown():
    pass                                     # 模块卸载时调用

def get_agile_module():
    return module
```

## AgileContext 依赖注入

Hook 函数**签名中类型注解为 `AgileContext` 的参数**（任意参数名）会被自动注入 agile 实例。同步/异步函数均支持，无需手动 await 判断。

```python
def state(_path, agile: AgileContext):       # 同时拿到 path 和 agile
    ...

async def poll(agile: AgileContext):         # 只拿 agile
    await agile.linfo("...")
```

AgileContext 方法（全部 async）：
- `await agile.vfs_write_symbolic(path, func, writable=False, should_be_included_in_search=True)`
- `await agile.vfs_set_write_handler(path, func)` / `await agile.vfs_set_edit_handler(path, func)` / `await agile.vfs_delete(path)`
- `await agile.event_fire(event_name, data, recall_description="Agent 可读的描述", lifespan=7200)` —— 触发事件唤醒你自己，之后 Agent 会收到 recall_description 描述的事件
- `await agile.log(level, msg)` / `linfo` / `ldebug` / `lwarning` / `lerror` / `lcritical`
- `agile.storage` —— 本模块的持久化 KV 存储（详见下节「AgileStorage」）

### AgileStorage（模块级 KV 持久存储）

`agile.storage` 是每个模块独立的键值存储，数据落盘在
`~/.faustbot/plugin_data/agile-engine/<模块名>.json`，跨重启、reload、卸载/禁用均保留。

两个方法（**同步**实现，同步/异步 hook、interval 线程里都可直接调用，无需 await；内部已加锁，多线程安全）：

- `agile.storage.get(key, default=None)` —— 读取；键不存在返回 `default`
- `agile.storage.set(key, value)` —— 写入并立即落盘；`value` 必须可 JSON 序列化

跨 get/set 的读改写序列需要原子性时，用 `with agile.storage:` 持锁（可重入）：

```python
@module.registerInterval(60)
async def poll(agile: AgileContext):
    with agile.storage:
        n = agile.storage.get("count", 0)
        agile.storage.set("count", n + 1)
```

注意：
- 不要在 `with agile.storage:` 块内做耗时操作（会阻塞其他线程的读写）
- 卸载/禁用模块**不会**删除存储文件（数据持久保留，重装即恢复）

## cacheStrategy（内容节点缓存策略）

`@module.vfsContentFunc(path, cacheStrategy=...)`，四种取值：

| 策略 | 行为 |
|---|---|
| `cache@N` | N 秒内重复读取返回缓存内容（默认 `cache@10`） |
| `wait@N` | 距上次读取不足 N 秒则等待到满 N 秒再执行（节流，总是最新） |
| `error@N` | 距上次读取不足 N 秒则返回错误信息（限流拒绝） |
| `nocache` | 每次读取都执行 |

示例：轮询外部端口用 `cache@1` 防高频重复请求；外部 API 用 `wait@30` 自然节流。

## 完整示例（封装本地端口数据 + 定时报警）

```python
import json, urllib.request
from agile_base import AgileModule, AgileContext

SVC = "http://127.0.0.1:8111"   # 示例：某本地服务
module = AgileModule("warthunder", "实时数据助手")

def _fetch(path):
    try:
        with urllib.request.urlopen(SVC + path, timeout=1.0) as r:
            return json.loads(r.read().decode("utf-8", "replace"))
    except Exception as exc:
        return {"__error__": str(exc)}      # 服务未运行 → 优雅降级

@module.vfsContentFunc("/warthunder/state", cacheStrategy="cache@1")
def state(_path):
    return json.dumps(_fetch("/state"), ensure_ascii=False)

@module.registerInterval(2)
async def watch(agile: AgileContext):
    data = _fetch("/indicators")
    if data.get("__error__"):
        return                               # 游戏未运行，安静跳过
    if float(data.get("flap", 0)) >= 90:     # 阈值报警
        await agile.event_fire("warthunder::flap_danger", data,
                               "襟翼震颤接近极限，提醒用户收襟翼")

def get_agile_module():
    return module
```

## 注意事项

- 模块在 Agent 进程内执行，无沙箱；请只做数据读取与逻辑，**不要**在模块里执行任意系统命令或模拟键鼠
- 模块代码异常不会拖垮系统：内容节点异常返回错误文本并记入 `log/errors`，interval 异常记录后继续
- 每个模块默认**每分钟最多触发 60 次 trigger**（60 秒滑动窗口），超限时 `event_fire` 抛错并记入 `log/errors`；用 `agileOperate(action="limit", name="{name}", value="{n}")` 调整（0/负数 = 不限制）
- 触发上限**持久化**在 `~/.faustbot/agile-modules/{name}.limit`：unload→load、disable→enable、reload、重启后端后均保留
- 卸载/禁用会自动删除模块注册的 VFS 节点、取消定时任务（可逆转）
- 同路径的 content + write hook 可以共存（内部按类型区分）
- 模块路径写法 `faustbot://xxx` 与 `/xxx` 均可，统一指向 VFS 内部路径
- 需要读取FaustBot系统源码时，用 `read("sourceCode://backend/...")` 对照实现（目录自动列出内容）

## 与用户交互:

不要说:
    例子:
    "我创建了一个Agile模块,有什么功能,有什么VFS节点,有什么触发器"
->> 这是在描述技术,用户不想看

应该说:
    例子:
    "好了[口语化的表达],我现在可以帮你看天气了[可以帮助用户干什么],我....[按照角色设定自由发挥]"
->> 这是在用agile适应和满足用户的需求
