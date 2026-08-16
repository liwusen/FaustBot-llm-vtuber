// Live2D 模型目录布局检测与摊平（纯 Node，无 electron 依赖，可单测）
// 背景：部分旧式 Live2D 模型把 *.model3.json 放在嵌套子目录（如
//   <modelDir>/<char>/runtime/<char>.model3.json），而 soullink profile
//   generator 只扫描模型目录顶层，遇到这种布局会生成失败。
// 策略：启动时检测嵌套目录 → 弹窗警告用户 → 单角色目录可自动摊平，
//       拒绝加载未处理的嵌套目录模型。
'use strict';

const fs = require('fs');
const path = require('path');

const MAX_SCAN_DEPTH = 4; // 递归查找 model3.json 的最大深度（相对模型目录）
const MODEL3_RE = /\.model3\.json$/i;

/**
 * 递归收集目录内所有 *.model3.json 的相对路径（深度受限，跳过隐藏目录）。
 * @param {string} absRoot 扫描起点（绝对路径）
 * @param {number} depth
 * @returns {string[]} 相对路径列表（POSIX 风格，按路径排序）
 */
function findModel3Files(absRoot, depth = 0) {
  if (depth > MAX_SCAN_DEPTH) return [];
  let entries;
  try {
    entries = fs.readdirSync(absRoot, { withFileTypes: true });
  } catch {
    return [];
  }
  const found = [];
  for (const entry of entries) {
    if (entry.name.startsWith('.')) continue;
    const abs = path.join(absRoot, entry.name);
    if (entry.isFile()) {
      if (MODEL3_RE.test(entry.name)) found.push(path.basename(abs));
    } else if (entry.isDirectory()) {
      for (const rel of findModel3Files(abs, depth + 1)) {
        found.push(path.join(entry.name, rel).split(path.sep).join('/'));
      }
    }
  }
  return found.sort();
}

/**
 * 扫描模型根目录，返回嵌套布局的模型目录信息。
 * @param {string} modelsRoot 模型根（如 ~/.faustbot/models/2D）
 * @returns {Array<{modelDir: string, model3Paths: string[], flattable: boolean}>}
 *   嵌套 = 顶层无 *.model3.json 但子目录有；flattable = 只有一份 model3（可安全摊平）。
 */
function scanNestedModelDirs(modelsRoot) {
  const result = [];
  let entries;
  try {
    entries = fs.readdirSync(modelsRoot, { withFileTypes: true });
  } catch {
    return result;
  }
  for (const entry of entries) {
    if (!entry.isDirectory() || entry.name.startsWith('.')) continue;
    const dirAbs = path.join(modelsRoot, entry.name);
    let topEntries;
    try {
      topEntries = fs.readdirSync(dirAbs, { withFileTypes: true });
    } catch {
      continue;
    }
    const hasTopModel3 = topEntries.some(
      (e) => e.isFile() && MODEL3_RE.test(e.name)
    );
    if (hasTopModel3) continue; // 正常布局
    const model3Paths = findModel3Files(dirAbs, 1);
    if (model3Paths.length === 0) continue; // 无模型文件，忽略
    result.push({
      modelDir: entry.name,
      model3Paths,
      flattable: model3Paths.length === 1,
    });
  }
  return result;
}

/**
 * 摊平单个嵌套模型目录：把唯一 model3.json 所在目录的**全部内容**（含子目录）
 * 原样平移到模型目录顶层，再删除空的原目录链。
 * 平移保持目录相对结构，因此 model3.json 内部的相对引用（motion/、texture 等）依然成立。
 * @param {string} modelDirAbs 模型目录绝对路径
 * @param {string} model3Rel model3.json 相对路径（scanNestedModelDirs 的 model3Paths[0]）
 * @returns {{ok: boolean, error?: string}}
 */
function flattenNestedModelDir(modelDirAbs, model3Rel) {
  try {
    // 只允许单角色摊平：整个模型目录必须恰好一份 model3.json。
    // 多角色（如 hijiki + tororo 两个 runtime）摊平会移动冲突，且摊平一个后
    // 顶层出现 model3 会被误判为正常布局、漏掉其余角色——因此直接拒绝，提示手动整理。
    const all = findModel3Files(modelDirAbs, 1);
    if (all.length !== 1) {
      return {
        ok: false,
        error: `目录包含 ${all.length} 份 model3.json（多角色），无法自动摊平，请手动整理为顶层单模型布局`,
      };
    }
    const srcDir = path.resolve(modelDirAbs, path.dirname(model3Rel));
    if (!srcDir.startsWith(path.resolve(modelDirAbs) + path.sep)) {
      return { ok: false, error: 'model3 路径越界' };
    }
    const entries = fs.readdirSync(srcDir, { withFileTypes: true });
    // 逐项移动到模型目录顶层；任何同名冲突都中止（不覆盖用户数据）
    for (const entry of entries) {
      if (entry.name.startsWith('.')) continue;
      const src = path.join(srcDir, entry.name);
      const dst = path.join(modelDirAbs, entry.name);
      if (fs.existsSync(dst)) {
        return { ok: false, error: `目标已存在同名条目: ${entry.name}（可能含多个角色，请手动整理）` };
      }
      fs.renameSync(src, dst);
    }
    // 清理空目录链（runtime → 角色目录 → ...）
    let cur = srcDir;
    while (cur.startsWith(path.resolve(modelDirAbs) + path.sep)) {
      try {
        fs.rmdirSync(cur); // 仅删除空目录
      } catch {
        break;
      }
      cur = path.dirname(cur);
    }
    return { ok: true };
  } catch (e) {
    return { ok: false, error: String(e && e.message ? e.message : e) };
  }
}

module.exports = {
  MAX_SCAN_DEPTH,
  findModel3Files,
  scanNestedModelDirs,
  flattenNestedModelDir,
};
