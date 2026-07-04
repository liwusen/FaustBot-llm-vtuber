# CSV 文件处理技能

本技能指导 AI 如何使用现有工具读写和处理 CSV 文件。

## 读取 CSV 文件

使用 `read` 工具读取 CSV 文件内容，然后用 Python 的 `csv` 模块或 pandas 解析。推荐使用 eval（py）运行代码：

```python
import csv, io
text = read("data.csv")
reader = csv.DictReader(io.StringIO(text))
rows = list(reader)
```

对于大文件，可逐行处理避免内存爆炸。

## 写入 CSV 文件

构造数据后使用 `write` 工具写出：

```python
import csv, io
buf = io.StringIO()
writer = csv.DictWriter(buf, fieldnames=["col1", "col2"])
writer.writeheader()
writer.writerows(rows)
write("output.csv", buf.getvalue())
```

## 合并与过滤

读取多个 CSV 后用 Python 的 dict/列表操作合并或按条件过滤行，再写出结果。

## 示例用法

当用户说"帮我处理 data.csv，筛选出 status=active 的行并输出到 active.csv"时，按上述读取→过滤→写入流程执行，每一步用 eval 调用 Python 完成。
