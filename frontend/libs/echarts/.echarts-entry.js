// Entry point for esbuild — bundles ECharts 5 + echarts-wordcloud into a single IIFE.
// Used by: npm run build:echarts (also part of npm run build:bundle)
// Output: libs/echarts/echarts-bundle.js  (构建产物，不入库)
//
// 记忆页可视化(词云/时间线/Treemap)与插件面板都读 window.echarts，
// 因此不再从 CDN 动态加载：离线也能渲染。
import * as echarts from 'echarts';
// 词云 series 通过副作用注册到同一个 echarts 核心上
import 'echarts-wordcloud';

window.echarts = echarts;
