#!/usr/bin/env node
/**
 * 构建 pixi-live2d bundle，并对 pixi-live2d-display/cubism4 注入"遮罩管理器空值保护"。
 *
 * 背景：pixi-live2d-display 0.5.0-beta 的 Cubism4InternalModel.updateWebGLContext 无条件访问
 *   `renderer._clippingManager._currentFrameNo`，而 core 渲染器（CubismRenderer_WebGL.initialize）
 *   只在模型使用剪裁遮罩（model.isUsingMasking()）时创建 _clippingManager。
 *   对不使用遮罩的模型，GL context 变化（首次渲染 / 窗口 resize / 上下文重建）会抛
 *   `Cannot set properties of undefined (setting '_currentFrameNo')`。
 *
 * 注入方式：构建期对库源码做精确字符串替换（不修改 node_modules），
 *   仅把"遮罩状态重置"两行包进 `if (this.renderer._clippingManager)`：
 *   - 使用遮罩的模型：_clippingManager 存在 → 行为与未打补丁完全一致；
 *   - 不使用遮罩的模型：跳过无意义的遮罩重置（渲染路径本就有 _clippingManager 空值保护），其余原样。
 *   若库升级导致模式不匹配，构建直接报错（避免静默失去补丁）。
 * 
 * 我就不应该用一个已经停止更新n年的Live2D库!!!!
 */
'use strict';

const path = require('path');
const fs = require('fs');
const esbuild = require('esbuild');

const ROOT = path.resolve(__dirname, '..');

const OLD_SNIPPET = [
  '    this.renderer._clippingManager._currentFrameNo = glContextID;',
  '    this.renderer._clippingManager._maskTexture = void 0;',
].join('\n');
const NEW_SNIPPET = [
  '    if (this.renderer._clippingManager) {',
  '      this.renderer._clippingManager._currentFrameNo = glContextID;',
  '      this.renderer._clippingManager._maskTexture = void 0;',
  '    }',
].join('\n');

const cubism4ClippingGuard = {
  name: 'cubism4-clipping-guard',
  setup(build) {
    build.onLoad({ filter: /[\\/]pixi-live2d-display[\\/]dist[\\/]cubism4\.es\.js$/ }, (args) => {
      const code = fs.readFileSync(args.path, 'utf8');
      if (!code.includes(OLD_SNIPPET)) {
        throw new Error('[build-live2d] cubism4 updateWebGLContext 模式不匹配，遮罩保护注入失败（库可能已升级，请检查 scripts/build-live2d.js）');
      }
      return { contents: code.split(OLD_SNIPPET).join(NEW_SNIPPET), loader: 'js' };
    });
  },
};

esbuild
  .build({
    entryPoints: [path.join(ROOT, 'libs', 'pixi-live2d', '.pixi-live2d-entry.js')],
    bundle: true,
    platform: 'browser',
    format: 'iife',
    outfile: path.join(ROOT, 'libs', 'pixi-live2d.bundle.js'),
    minifySyntax: true,
    plugins: [cubism4ClippingGuard],
    logLevel: 'info',
  })
  .catch((e) => {
    console.error(e);
    process.exit(1);
  });
