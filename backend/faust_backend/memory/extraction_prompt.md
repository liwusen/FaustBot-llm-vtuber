# Task: 实体与关系抽取

你的任务: 严格遵循如下指示,从给定文本中抽取实体和关系。

你抽取的实体有如下要求:

1. 内容准确，不得偏离文本

2. 内容精炼，应该是文本中的核心内容，如果只是简单在文本中提到，不应该输出为一个实体

## 实体类型
- person: 人物
- place: 地点
- event: 事件
- concept: 抽象概念/主题/知识
- object: 具体物体/工具
- document: 文档/文件

## 关系类型
- relates_to: 通用关联
- part_of: A是B的一部分
- located_at: A位于B
- created_by: A由B创建
- mentions: A提到了B
- references: A引用了B

## 实体命名规则

- 具体,避免歧义
  比如 文档中提到 小明有一只猫，那么应该把实体命名为 小明的猫 而不是 猫

## 输出格式

必须严格按照JSON格式输出
```json
{"entities": [{"name": "...", "type": "...", "description": "...", "properties": {}}], "relations": [{"source": "...", "target": "...", "type": "..."}]}
```
- description 字段为实体的一到两句自然语言描述，概括其核心属性或与上下文的关系。必须为中文。
