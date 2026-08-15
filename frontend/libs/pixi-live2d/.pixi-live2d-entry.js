// 从 NPM 引入 PIXI.js 与 pixi-live2d-display，用 esbuild 打包为 iife。
// 输出 libs/pixi-live2d.bundle.js，挂载全局：
//   - window.PIXI（含 PIXI.live2d.Live2DModel / PIXI.live2d.HitAreaFrames）
//   - window.Live2DModel
// 运行前需先加载 libs/live2d.min.js（Cubism 2 core，window.Live2D）与
// libs/live2dcubismcore.min.js（Cubism 4 core，window.Live2DCubismCore），
// pixi-live2d-display 主入口在加载时会检查这两个全局。
//
// 注意：ES module 命名空间对象不可变，无法直接挂 PIXI.live2d，
// 因此把 pixi.js 的可枚举导出拷贝到一个可扩展的全局对象。
import * as PIXI_NS from 'pixi.js';
import { Live2DModel } from 'pixi-live2d-display';
import { HitAreaFrames } from 'pixi-live2d-display/extra';

const PIXI = {};
for (const key of Object.keys(PIXI_NS)) {
  PIXI[key] = PIXI_NS[key];
}
PIXI.live2d = { Live2DModel, HitAreaFrames };
window.PIXI = PIXI;
window.Live2DModel = Live2DModel;
