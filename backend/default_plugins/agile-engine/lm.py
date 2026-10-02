"""
Agile Log Manager
from faust_backend.plugin_system import PluginContext
"""
import time
import threading
from pydantic import BaseModel, Field
from faust_backend.logger import get_logger


class LogContent(BaseModel):
    timestamp: float = Field(..., description="日志时间戳")
    agile_from: str = Field(..., description="日志来源")
    level: str = Field(..., description="日志级别")
    message: str = Field(..., description="日志内容")
    extra: dict = Field(default_factory=dict, description="额外信息")

BUFFER_SIZE = 1000
logs: list[LogContent] = []
# 必须是线程锁：日志来自 backend 主事件循环，也来自每个模块自己的 interval 线程/事件循环。
# 用 asyncio.Lock 时，第二个 loop 会 await 另一个 loop 上的 future，而 release 又发生在第一个
# loop 的线程里——跨 loop 唤醒会抛 "Non-thread-safe operation"，等待方永远等不到（实测会卡死
# interval hook）。临界区里没有任何 await，同步锁足够。
lock = threading.Lock()
LEVEL_MAP={
    "DEBUG": 10,
    "INFO": 20,
    "WARNING": 30,
    "ERROR": 40,
    "CRITICAL": 50
}


class AgileLogManager:
    def __init__(self):
        self.logger = get_logger(f"faust.plugins.agile-engine.alm")

    async def log(self,agile_from:str,level:str,message:str,extra:dict={}):
        if level not in LEVEL_MAP:
            raise ValueError(f"Invalid log level: {level}")
        log_content = LogContent(
            timestamp = time.time(),
            agile_from = agile_from,
            level = level,
            message = message,
            extra = extra
        )
        self.logger.info(log_content.model_dump())
        with lock:
            logs.append(log_content)
            if len(logs) > BUFFER_SIZE:
                logs.pop(0)

    async def getLog(self,agile_from:str=None,level:str=None,start_time:float=None,end_time:float=None):
        with lock:
            filtered_logs = logs
            if agile_from is not None:
                filtered_logs = [log for log in filtered_logs if log.agile_from == agile_from]
            if level is not None:
                filtered_logs = [log for log in filtered_logs if LEVEL_MAP[log.level] >= LEVEL_MAP[level]]
            if start_time is not None:
                filtered_logs = [log for log in filtered_logs if log.timestamp >= start_time]
            if end_time is not None:
                filtered_logs = [log for log in filtered_logs if log.timestamp <= end_time]
            return filtered_logs

    @classmethod
    async def formatLog(cls,log:LogContent):
        return f"[{time.strftime('%Y-%m-%d %H:%M:%S', time.localtime(log.timestamp))}] [{log.agile_from}] [{log.level}] {log.message} {log.extra}"

    @classmethod
    async def formatLogs(cls,logs:list[LogContent]):
        return [await cls.formatLog(log) for log in logs]