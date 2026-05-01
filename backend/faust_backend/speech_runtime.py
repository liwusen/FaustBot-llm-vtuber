"""
桥接模块: 语音运行时委托给 speech/ 包。
原有消费者无需修改。
"""
from faust_backend.speech import *  # noqa: F401, F403
