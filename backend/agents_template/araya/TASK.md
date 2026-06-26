# Task

每次被触发时按以下顺序工作：

1. 使用 `read("memory://records")` 和 `read("memory://diary")` 读取最近活动相关的节点。
2. 获取自上次触发以来变更的节点（changed-nodes）。
3. 使用 `edit("memory://path", patch)` 按需更新 tags、score patch、节点内容。
4. 检查 knowledge graph：
   - 对新的 records/diary 内容提取实体和关系
   - 清理重复/冗余实体
   - 确保相关实体链接到其来源文件
5. 维护 /auto_index.md：
   - 使用 `edit("memory://auto_index", patch)` 覆盖写入最新摘要与分类索引。
   - Auto Index至少需要包含的内容：
     - 对目录结构的介绍
     - 你打的Tag的详细解释
6. 仅在必要时改动记忆库，避免无意义重写或导致重要内容被删除。
7. 维护 /audit_log/{你运行的时间,YYYYMMDD_HHMMSS}.md：
   - 使用 `write("memory://audit_log/...", content)` 记录你对记忆库进行的操作。
8. 如果需要记住什么，使用 `write("memory://araya_private_data/...", content)` 写入。
