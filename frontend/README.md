Faust 前端 Live2D Demo

目的
- 提供一个最小前端，用来展示 Live2D 模型（类似桌宠）。

使用说明
1. 将 Live2D 模型文件放入本目录下的 `2D/` 文件夹（例如 `2D/model.model3.json` 和对应的资源目录）。
2. 用浏览器打开 `index.html`（推荐使用 Chrome/Edge）。
   - 如果模型资源使用相对路径，直接打开文件可能受 CORS 限制，建议使用一个本地静态服务器：
     - Python 简单服务器：在 `faust/frontend` 目录运行：
       ```powershell
       python -m http.server 8080
       ```
       然后访问 `http://localhost:8080/`。
3. 在页面底部输入框填写模型文件路径（例如 `2D/your_model.model3.json`）然后点击“加载模型”。

注意
- 该 demo 依赖外网 CDN（pixi.js / pixi-live2d-display）。若在离线环境，需将相应库下载到本地并修改 `index.html` 引用。
- 该页面只是展示用途，不带 Electron 窗口层级/鼠标穿透等桌宠特性；如需集成到桌面请用 Electron 并参考主项目 `live-2d` 的实现。
