# 为这个项目添加直播功能

## Step 1: b站弹幕读取

查看并且使用 `backend\faust_backend\blivedm` 库。使用它的Web端模式，启动后监听指定B站直播间的弹幕消息发送，要求可以配置直播间编号和SESSDATA两个参数，并且可以选择是否启用。

接收到弹幕后，把这作为一个Event Trigger 放到Trigger 队列中进行处理，你需要在main.py的Command WS Loop为它专门编写一个handler,把它格式化为更简单的`用户名:消息`的格式



注意 直播时的弹幕消息也需要在前端ASR结果的位置显示出来



你也需要为Faust Agent编写对应的提示词

## Step 2: 直播模式

为前端添加一个进入直播模式的按钮，进入后启动Step 1中提到的功能。

前端打开一个单独的直播控制窗口，可以设置b站弹幕黑名单词，设置TTS黑名单词，查看再Trigger队列中的所有 弹幕 类型的任务，并且可以删除指定的 弹幕 Trigger



在直播模式下，模型的以下能力被完全禁止使用

- Python 执行，系统命令执行

- Trigger 创建，删除，修改操作

- Skill 安装

- 知识库删除

- 读取fnmatch不符合`*/agents/*.md`的文件

- 系统文件写入



请把直播模式的限制和直播时多多和用户交互的要求写入AI提示词



参考 document/ai_prompt.md 这个编码规则


