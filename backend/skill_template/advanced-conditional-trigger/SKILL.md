# 高级条件触发器

本技能指导你如何编写 **py-eval 条件触发器**：用 Python 表达式组合时间、空闲度等运行时常量，在条件满足时唤醒自己。


## 触发器选型

| 需求 | 用哪个 |
|---|---|
| 固定时刻（如 23:00） | type=datetime，target="YYYY-MM-DD HH:MM:SS" |
| 固定周期（如每 10 分钟） | type=interval，interval_seconds=600 |
| 条件组合（如深夜且空闲） | type=py-eval，eval_code="表达式" |

用 triggerAddTool 添加，通用字段：id、type、recall_description、lifespan、run_background、priority。


## py-eval 可用常量

eval_code 中可直接引用以下常量（只读快照，每 0.5 秒重算）：

| 常量 | 含义 |
|---|---|
| HOUR / MINUTE / SECOND | 当前时/分/秒（0-23 / 0-59 / 0-59） |
| WEEKDAY | 星期几，0=周一 … 6=周日 |
| MONTH / DAY | 月（1-12）/ 日 |
| EPOCH | 当前 Unix 时间戳（秒） |
| FREETIME_MIN | 距上次用户交互的分钟数；从未交互过时为极大值 |
| USER_IDLE_SEC | 同上，秒精度；用户每次发消息瞬间归零 |

示例：

```python
# 深夜且用户空闲半小时以上
HOUR == 23 and FREETIME_MIN > 30

# 周末上午且空闲 2 小时
WEEKDAY >= 5 and HOUR >= 10 and FREETIME_MIN > 120

# 工作日整点报告
MINUTE == 0 and WEEKDAY < 5
```


## 红线

- eval_code 每 0.5 秒执行一次：必须轻量、纯表达式、无副作用、不做 IO（不要读写文件/发请求）
- 只用上述常量与 Python 内建函数做纯计算；不要 import
- 返回 truthy 即触发；priority="batched" 的触发器连续满足条件时会在消费侧合并为一次唤醒，不会刷屏
- 想要"只触发一次"的条件（如整点报时）用 datetime 类型更可靠
