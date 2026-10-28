import type { Component } from 'vue';
import PluginCards from '@/plugin/PluginCards.vue';
import PluginPage from '@/plugin/PluginPage.vue';
import { usePluginsStore } from '@/stores/plugins';
import OverviewModule from './overview/OverviewModule.vue';
import AiModule from './ai/AiModule.vue';
import ModelModule from './model/ModelModule.vue';
import SpeechModule from './speech/SpeechModule.vue';
import AgentModule from './agent/AgentModule.vue';
import MemoryModule from './memory/MemoryModule.vue';
import ArayaModule from './araya/ArayaModule.vue';
import ComponentsModule from './components/ComponentsModule.vue';
import McpModule from './mcp/McpModule.vue';
import TriggersModule from './triggers/TriggersModule.vue';
import SkillsModule from './skills/SkillsModule.vue';
import PluginsModule from './plugins/PluginsModule.vue';
import AdvancedModule from './advanced/AdvancedModule.vue';
import RuntimeModule from './runtime/RuntimeModule.vue';
import UnknownModule from './UnknownModule.vue';

export interface ModuleResolution {
  component: Component;
  props: Record<string, unknown>;
  isPlugin: boolean;
}

/** 内置模块的固定注册表；插件页面不能覆盖同 id 的内置模块（与旧调度层一致）。 */
const BUILTIN_MODULES: Record<string, Component> = {
  overview: OverviewModule,
  ai: AiModule,
  live2d: ModelModule,
  speech: SpeechModule,
  agent: AgentModule,
  memory: MemoryModule,
  araya: ArayaModule,
  components: ComponentsModule,
  mcp: McpModule,
  triggers: TriggersModule,
  skills: SkillsModule,
  plugins: PluginsModule,
  advanced: AdvancedModule,
  runtime: RuntimeModule,
};

export function resolveModule(moduleId: string): ModuleResolution {
  const builtin = BUILTIN_MODULES[moduleId];
  if (builtin) return { component: builtin, props: {}, isPlugin: false };

  const plugins = usePluginsStore();
  const page = plugins.pages.find((p) => p.id === moduleId);
  if (page && typeof page.render === 'function') {
    return { component: PluginPage, props: { moduleId }, isPlugin: true };
  }
  const cards = plugins.cards.filter((c) => c.moduleId === moduleId);
  if (cards.length) {
    return { component: PluginCards, props: { moduleId }, isPlugin: true };
  }
  return { component: UnknownModule, props: { moduleId }, isPlugin: true };
}
