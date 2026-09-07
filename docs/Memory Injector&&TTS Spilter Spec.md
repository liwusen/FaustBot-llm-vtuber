## Memory Injector Plugin Spec

参考`default_plugins/`下我写的草稿

有两个工作模式:

1. Lite Mode
   
   使用Jieba分词用户消息,BM25本地搜索

2. Full Mode
   
   类似RAG,基于记忆的混合检索,你需要自己想办法处理:用户输入了一个低信息熵的内容/橛子,比如"你好",或者,"谢谢.请问LSTM是什么”,在两句话中,你好和谢谢不应该被送去搜索

设计要求:

1. 实现附加给LLM的记忆文档n轮内不重复的功能

2. 附加给LLM一部分文档/文档元数据

3. top_k<=3

## Yet Another TTS Spilter Algorithm Spec

TTS使用启发式算法对LLM输出进行分块,

目标:流式处理文本,尽可能使得一个TTS块的长度为 理想TTS块单位长(单位Token),尽可能在标点符号/换行符处分句,除非长度会超过理想TTS块单位长长度的两倍



Token 与 字符换算规则:

1字母=0.5 Token

1CJK汉字=1 Token



前端Configer需要增加 理想TTS块单位长(单位Token) 配置

这是一个int,10~100之间的滑块,需要支持动态预览,即这个滑块的上方显示一段长文字,拖动滑块时动态展示分段效果




