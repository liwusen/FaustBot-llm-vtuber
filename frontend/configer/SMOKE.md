# Configer Vue 冒烟记录

日期：2026-10-28 · 分支：`refactor/configer-vue` · 后端：`127.0.0.1:13900`

## 真实后端 + Electron

启动真实后端与 Electron，配置窗口加载 `frontend/configer-dist/index.html`，通过 CDP 驱动检查：

- 内置模块：`overview`、`ai`、`live2d`、`speech`、`agent`、`memory`、`araya`、`components`、`mcp`、`triggers`、`skills`、`plugins`、`advanced` 全部可切换、有数据、无「模块加载失败」。
- 真实插件：`agile-engine`、`desktop-mood`、`agent-communicate`、`emotion-engine`、`dev-debugger`、`song-studio` 共 6 个页面由 `/faust/admin/plugins/assets` 注入并正常渲染；另加载 4 个插件卡片。
- 控制台：无 `Runtime.exceptionThrown`，只有 Electron CSP 开发警告。
- 持久模块生命周期：overview → memory → overview → araya 切换正常；无 dirty 点「重新加载」后 KeepAlive 整体重建，再进入 memory/overview 正常，无异常。
- 插件注入独立验证：本地插件脚本 `addPage` 后导航即时出现，`render(container)` 正常；`setPluginDirty` 后顶栏显示「1 项配置已修改」；切换到 overview 后 persistence 被注销、dirty 归零。

## 自动化结果

```powershell
cd frontend
npm run typecheck:configer   # exit 0
npm run test:configer        # 43 files / 407 tests passed
npm run build:configer       # configer-dist 构建成功
npm run check-js             # All files OK
```

```powershell
$env:PYTHONUTF8='1'; $env:PYTHONIOENCODING='utf-8'
.runtime\python.exe -m pytest backend\tests -q
# 1236 passed, 4 skipped
```

> 后端 `test_processor_manager.py::test_invoke_roundtrip_and_stdout_capture` 在未设置 UTF-8 环境时会把子进程中文 stdout 解码坏；设置 `PYTHONUTF8=1` 后通过。与本前端重构无关。

## 建议人工复核（未自动执行破坏性操作）

1. 顶栏「保存 / 应用」真实写盘（尤其 provider、Live2D、插件 save 钩子）——自动化只用 mock/单测验证了 payload 和顺序。
2. 文件选择对话框、ZIP 安装、更新下载/应用。
3. 记忆编辑器在真实文件上的保存/放弃守卫、Canvas 图谱拖拽/缩放/BFS。
4. Araya 手动触发一次真实运行（会调用 LLM 并写记忆）。
5. Electron 打包产物 `win-unpacked` 启动后的配置窗口。
