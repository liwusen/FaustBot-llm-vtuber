// esbuild 入口：把 ESM 的 profile-generator 与 engine/internal 打包为 CJS，
// 供 electron-main.js 在 asar 环境下直接 require（规避运行时 ESM 动态 import 在打包后的风险）。
// 产物 libs/soullink/profile-generator.cjs（platform=node, format=cjs）
import { Live2DProfileAutoGenerator, STANDARD_PARAM_TABLE, profileGeneratorVersion, resolveStandard } from '@soullink-emotion/profile-generator';
import { validateModelProfile } from '@soullink-emotion/engine/internal';

export { Live2DProfileAutoGenerator, STANDARD_PARAM_TABLE, profileGeneratorVersion, resolveStandard, validateModelProfile };
