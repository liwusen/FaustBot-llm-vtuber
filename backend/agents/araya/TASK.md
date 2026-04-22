# Task

每次被触发时按以下顺序工作：

1. 先读取 records/ 和 diary/ 中与最近活动相关的节点。
2. 获取自上次触发以来变更的节点。
3. 按需更新 tags、score patch、节点内容。
4. 维护 /auto_index.md，覆盖写入最新摘要与分类索引。
    - Auto Index至少需要包含的内容
        - 对目录结构的介绍
        - 你打的Tag的详细解释
5. 仅在必要时改动知识库，避免无意义重写，或者导致重要内容被删除
6. 维护 /audit_log/{你运行的时间,YYYYMMDD_HHMMSS}.md
    - 记录你对知识库进行的操作
7. 如果你需要记住什么，请写入/araya_private_data/目录下