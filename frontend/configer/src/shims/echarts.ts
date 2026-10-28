import * as echarts from 'echarts';
import 'echarts-wordcloud';

// 插件与旧模块都通过全局 window.echarts 取实例。
window.echarts = echarts as unknown as typeof import('echarts');

export default echarts;
