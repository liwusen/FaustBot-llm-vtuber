import { createApp } from 'vue';
import { createPinia, setActivePinia } from 'pinia';
import './styles/config-window.css';
import '../../libs/ui-icons.css';
import '../../libs/ui-icons.js';
import '../../libs/tts-splitter.js';
import './styles/onboarding.css';
import './shims/echarts';
import './shims/codeMirror';
import App from './App.vue';
import { installPluginHost } from './plugin/pluginHost';
import { installDeeplinkBridge } from './deeplink';

const pinia = createPinia();
setActivePinia(pinia);

// 必须在任何插件脚本加载、任何组件挂载之前安装。
installPluginHost();
installDeeplinkBridge();

window.configAppUI = window.configAppUI || {};

createApp(App).use(pinia).mount('#app');
