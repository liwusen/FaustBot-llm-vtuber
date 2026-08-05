# 手动更新指南（推荐）

> 当应用内自动下载更新缓慢或失败时，推荐使用本方式：从 **ModelScope** 下载更新包（速度快，通常可达 10MB/s 以上），再在 FaustBot 中选择该包手动更新。

## 为什么推荐手动更新

- 自动更新从 GitHub 拉取发布包，网络不稳定时容易失败或缓慢。
- ModelScope（魔搭社区）国内访问快，浏览器直接下载大文件（约 275MB 的免运行时包）通常可达 10MB/s 以上。
- 手动更新只需在应用内选择本地文件，不依赖应用内下载通道，成功率更高。

## 第一步：从 ModelScope 下载更新包

1. 打开 FaustBot 的 ModelScope 发布仓库：
   **https://modelscope.cn/datasets/allenlee18/FaustBotCore**

2. 在仓库文件列表中，找到**最新版本**的免运行时更新包，文件名为：
   ```
   faust-V<版本号>-without-runtime.zip
   ```
   - 例如 `faust-V2.8.2-without-runtime.zip`
   - 认准 **`-without-runtime`** 字样，这是更新用的精简包（约 275MB）；
     `faust-V<版本号>-windows.zip` 是完整安装包（约 1GB），**不要**用它更新。

3. 点击该 zip 文件下载，保存到本地任意位置（建议放在「下载」目录方便查找）。

> 提示：ModelScope 仓库只保留最新版本（旧版本发布包会被自动清理），因此仓库里通常只有一个 `faust-Vxxx-without-runtime.zip`，直接下载即可。

## 第二步：在 FaustBot 中手动更新

1. 打开 FaustBot 的**配置窗口 → 概览页面**。
2. 找到「版本更新」卡片，点击 **「手动选择更新包」** 按钮。
3. 在弹出的文件选择对话框中，选中刚才下载的 `faust-V<版本号>-without-runtime.zip`。
4. 应用会校验更新包：
   - 文件名必须符合 `faust-Vx.y.z-without-runtime.zip` 规范；
   - 版本号必须高于当前安装版本；
   - 包必须完整（损坏的 zip 会被拒绝）。
5. 校验通过后显示「手动更新包已就绪: Vx.y.z」，点击 **「开始更新」**。
6. 弹出黑色 PowerShell 窗口，等待其执行完成。

## 常见问题

| 问题 | 解决 |
|------|------|
| 提示「文件名不符合更新包规范」 | 请确认选中的是 `faust-Vx.y.z-without-runtime.zip`，而不是 `-windows.zip` 完整包 |
| 提示「更新包版本不高于当前版本」 | 你下载的包版本不高于已安装版本，请确认 ModelScope 仓库是否为最新 |
| 提示「更新包已损坏」 | 下载不完整，请重新下载 |
| 找不到最新版本号 | 以 ModelScope 仓库文件列表中的最高版本号为准 |

## 相关

- [更新指南](updating.md)
- [安装指南](installation.md)
