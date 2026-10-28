<script setup lang="ts">
import { computed, onMounted, ref } from 'vue';
import EmptyState from '@/components/ui/EmptyState.vue';
import InfoCard from '@/components/ui/InfoCard.vue';
import SectionCard from '@/components/ui/SectionCard.vue';
import SimpleTableCard from '@/components/ui/SimpleTableCard.vue';
import SliderField from '@/components/ui/SliderField.vue';
import SwitchField from '@/components/ui/SwitchField.vue';
import TagListCard from '@/components/ui/TagListCard.vue';
import { API } from '@/api/endpoints';
import { cfgApi, openDirectoryDialog, openFileDialog } from '@/api/ipc';
import { formatApiError } from '@/api/errors';
import { SECRET_KEYS } from '@/config/constants';
import { getNumberFieldSpec } from '@/config/fieldSpecs';
import type { NumberFieldSpec } from '@/config/types';
import { loadPluginAssets } from '@/plugin/loadAssets';
import { useConfigStore } from '@/stores/config';
import { useModulesStore } from '@/stores/modules';
import { useUiStore } from '@/stores/ui';
import PluginHealthChart from './PluginHealthChart.vue';
import PluginLlmPickerModal from './PluginLlmPickerModal.vue';
import { pluginUpdates, selectedPluginId } from './pluginPageState';
import {
  isLlmConfigType,
  pluginBasicRows,
  pluginConfigDraftValues,
  pluginConfigPayload,
  pluginConfigSchema,
  pluginFieldText,
  pluginHealthLabel,
  pluginHealthRows,
  pluginLlmDescription,
  pluginLlmDisplayValue,
  pluginLlmHasValue,
  pluginMiddlewareRows,
  pluginPermissionTags,
  pluginToolRows,
  pluginUpdateBadge,
  pluginUpdateFor,
  pluginUpdatesBanner,
  pluginVersionText,
  resolvePluginSelection,
  type PluginConfigSchemaItem,
  type PluginInfo,
  type PluginUpdateEntry,
  type PluginUpdateData,
} from './pluginPageOps';

defineOptions({ name: 'module-plugins' });

interface PluginConfigField extends PluginConfigSchemaItem {
  key: string;
  type: string;
}

const config = useConfigStore();
const modules = useModulesStore();
const ui = useUiStore();

const plugins = ref<PluginInfo[]>([]);
const draft = ref<Record<string, unknown>>({});
const loadError = ref('');

const selectedPlugin = computed<PluginInfo | null>(
  () =>
    plugins.value.find((plugin) => String(plugin.id || '') === selectedPluginId.value) || null,
);

const configFields = computed<PluginConfigField[]>(() =>
  pluginConfigSchema(selectedPlugin.value)
    .map((item) => ({
      ...item,
      key: String(item.key || ''),
      type: String(item.type || 'str'),
    }))
    .filter((item) => item.key !== ''),
);

const proxyEnabled = computed(() => Boolean(config.publicConfig.PLUGIN_MARKET_USE_GH_PROXY));

function reportError(prefix: string, err: unknown): void {
  ui.showBanner('error', `${prefix}: ${formatApiError(err)}`);
}

async function loadPlugins(): Promise<void> {
  const data = await cfgApi<{ items?: PluginInfo[] }>('GET', API.plugins);
  const items = Array.isArray(data?.items) ? data.items : [];
  plugins.value = items;
  selectedPluginId.value = resolvePluginSelection(items, selectedPluginId.value);
  draft.value = pluginConfigDraftValues(selectedPlugin.value);
  loadError.value = '';
}

async function bootstrap(): Promise<void> {
  try {
    await loadPlugins();
  } catch (err) {
    loadError.value = formatApiError(err);
  }
}

async function reloadQuietly(): Promise<void> {
  try {
    await loadPlugins();
  } catch {
    // 保留当前画面，错误已经由具体操作回报。
  }
}

/** 旧 refreshPluginUi：重新注入插件前端资源 → 重拉插件数据 → 整页重渲染。 */
async function refreshPluginUi(): Promise<void> {
  await loadPluginAssets(true);
  await loadPlugins();
  modules.refreshCurrent();
}

function refresh(): void {
  modules.refreshCurrent();
}

async function reloadPlugins(): Promise<void> {
  try {
    await cfgApi('POST', API.pluginsReload, { apply_runtime: true, no_initial_chat: true });
    await refreshPluginUi();
  } catch (err) {
    reportError('重载插件失败', err);
  }
}

async function installFromZip(): Promise<void> {
  try {
    const zipPath = await openFileDialog({
      title: '选择插件 ZIP 文件',
      filters: [{ name: 'ZIP', extensions: ['zip'] }],
    });
    if (!zipPath) return;
    const overwrite = window.confirm('若插件已存在是否覆盖安装?');
    await cfgApi('POST', API.pluginsInstallZip, {
      zip_path: zipPath,
      overwrite,
      apply_runtime: true,
      no_initial_chat: true,
      reset_dialog: false,
    });
    await refreshPluginUi();
  } catch (err) {
    reportError('从 ZIP 安装插件失败', err);
  }
}

async function checkUpdates(): Promise<void> {
  try {
    const data = await cfgApi<PluginUpdateData>('GET', API.pluginMarketCheckUpdates);
    pluginUpdates.value = data;
    const notice = pluginUpdatesBanner(data);
    ui.showBanner(notice.type, notice.text);
    modules.refreshCurrent();
  } catch (err) {
    reportError('检查更新失败', err);
  }
}

async function packageZip(): Promise<void> {
  const target = selectedPlugin.value;
  if (!target) return;
  try {
    const outputDir = await openDirectoryDialog({ title: '选择 ZIP 输出目录' });
    const zipName = window.prompt('ZIP 文件名（可留空）') || '';
    const payload: Record<string, unknown> = { plugin_id: String(target.id || '') };
    if (outputDir) payload.output_dir = outputDir;
    if (zipName.trim()) payload.zip_name = zipName.trim();
    const data = await cfgApi<{ package?: { zip_path?: string } }>(
      'POST',
      API.pluginPackageZip,
      payload,
    );
    ui.showBanner('success', `插件已打包: ${(data && data.package ? data.package.zip_path : '') || '-'}`);
  } catch (err) {
    reportError('打包插件失败', err);
  }
}

async function toggleProxy(enabled: boolean): Promise<void> {
  try {
    await cfgApi('POST', API.config, { public: { PLUGIN_MARKET_USE_GH_PROXY: enabled } });
    config.publicConfig.PLUGIN_MARKET_USE_GH_PROXY = enabled;
    ui.showBanner(
      'success',
      enabled ? '插件市场已启用 gh-proxy 镜像。' : '插件市场已关闭 gh-proxy 镜像。',
    );
  } catch (err) {
    reportError('切换 gh-proxy 镜像失败', err);
  }
}

async function selectPlugin(plugin: PluginInfo): Promise<void> {
  const id = String(plugin.id || '');
  if (!id) return;
  selectedPluginId.value = id;
  try {
    await loadPlugins();
  } catch (err) {
    reportError('加载插件数据失败', err);
  }
}

async function togglePlugin(plugin: PluginInfo, enabled: boolean): Promise<void> {
  const id = String(plugin.id || '');
  if (!id) return;
  plugin.enabled = enabled;
  try {
    await cfgApi('POST', enabled ? API.pluginEnable(id) : API.pluginDisable(id), {
      apply_runtime: true,
      no_initial_chat: true,
      reset_dialog: true,
    });
    await refreshPluginUi();
  } catch (err) {
    reportError('切换插件状态失败', err);
    await reloadQuietly();
  }
}

async function deletePlugin(plugin: PluginInfo): Promise<void> {
  const id = String(plugin.id || '');
  if (!id) return;
  if (!window.confirm(`确定删除插件 ${id} ?`)) return;
  try {
    await cfgApi('DELETE', API.pluginDelete(id), null, {
      apply_runtime: 'true',
      reset_dialog: 'false',
      no_initial_chat: 'true',
    });
    await refreshPluginUi();
  } catch (err) {
    reportError('删除插件失败', err);
  }
}

async function updatePlugin(plugin: PluginInfo, entry: PluginUpdateEntry | null): Promise<void> {
  if (!entry) return;
  const id = String(plugin.id || '');
  if (!id) return;
  try {
    await cfgApi('POST', API.pluginMarketSync, {
      plugin_id: id,
      apply_runtime: true,
      no_initial_chat: true,
      reset_dialog: false,
    });
    const current = pluginUpdates.value;
    if (current && Array.isArray(current.updates)) {
      pluginUpdates.value = {
        ...current,
        updates: current.updates.filter((item) => String(item.id) !== id),
      };
    }
    ui.showBanner('success', `插件 ${id} 已更新到 ${entry.latest_version}`);
    await refreshPluginUi();
  } catch (err) {
    reportError('更新插件失败', err);
  }
}

async function saveSelectedConfig(): Promise<void> {
  const target = selectedPlugin.value;
  if (!target) return;
  const id = String(target.id || '');
  const payload = pluginConfigPayload(configFields.value, draft.value);
  try {
    await cfgApi('POST', API.pluginConfig(id), payload);
    await loadPluginAssets(true);
    await loadPlugins();
    ui.showBanner('success', `插件 ${id} 配置已保存并重载。`);
    modules.refreshCurrent();
  } catch (err) {
    reportError('保存插件配置失败', err);
  }
}

function updateFor(plugin: PluginInfo): PluginUpdateEntry | null {
  return pluginUpdateFor(pluginUpdates.value, String(plugin.id || ''));
}

function setDraft(key: string, value: unknown): void {
  draft.value[key] = value;
}

function inputText(event: Event): string {
  return (event.target as HTMLInputElement | HTMLTextAreaElement).value;
}

function numberSpec(field: PluginConfigField): NumberFieldSpec {
  return getNumberFieldSpec(field.key, Number(draft.value[field.key] ?? 0));
}

function sliderValue(field: PluginConfigField): number {
  const spec = numberSpec(field);
  return Number(draft.value[field.key] ?? spec.min ?? 0);
}

function isSecretKey(key: string): boolean {
  return SECRET_KEYS.has(key);
}

function openLlmPicker(field: PluginConfigField): void {
  const label = field.label || field.key;
  ui.openModal(
    PluginLlmPickerModal,
    {
      fieldKey: field.key,
      fieldLabel: label,
      currentValue: String(draft.value[field.key] ?? ''),
      onPick: (spec: string) => {
        setDraft(field.key, spec);
        ui.showBanner('success', `${label} 已选择 ${spec}（保存插件配置后生效）`);
      },
    },
    `模型选择 - ${label}`,
  );
}

onMounted(() => {
  void bootstrap();
});
</script>

<template>
  <SectionCard v-if="loadError" title="错误">
    <EmptyState :text="`模块加载失败: ${loadError}`" />
  </SectionCard>

  <template v-else>
    <SectionCard title="插件操作">
      <div class="toolbar">
        <button class="btn btn-ghost" type="button" @click="refresh">刷新</button>
        <button class="btn btn-secondary" type="button" @click="reloadPlugins">重载插件</button>
        <button class="btn btn-ghost" type="button" @click="installFromZip">从 ZIP 安装</button>
        <button class="btn btn-secondary" type="button" @click="checkUpdates">检查更新</button>
        <button class="btn btn-ghost" type="button" @click="packageZip">打包为 ZIP</button>
        <SwitchField
          :model-value="proxyEnabled"
          label="gh-proxy 镜像加速"
          @update:model-value="toggleProxy"
        />
      </div>
    </SectionCard>

    <article class="card full-span">
      <h3 class="card-title">插件列表</h3>
      <table class="simple-table plugin-list-table">
        <thead>
          <tr>
            <th>插件</th>
            <th>版本</th>
            <th>状态</th>
            <th>操作</th>
          </tr>
        </thead>
        <tbody>
          <tr
            v-for="plugin in plugins"
            :key="String(plugin.id || '')"
            :class="String(plugin.id || '') === selectedPluginId ? 'selected' : ''"
            @click="selectPlugin(plugin)"
          >
            <td class="plugin-cell">
              <div class="plugin-row-main">
                <PluginHealthChart :plugin="plugin" />
                <div class="plugin-row-text">
                  <strong class="plugin-row-title">{{ plugin.name || plugin.id }}</strong>
                  <div class="plugin-row-subtitle">{{ plugin.description || plugin.id }}</div>
                </div>
              </div>
            </td>
            <td>
              {{ pluginVersionText(plugin) }}
              <span v-if="updateFor(plugin)" class="tag-chip">
                {{ pluginUpdateBadge(updateFor(plugin)) }}
              </span>
            </td>
            <td>
              <span class="tag-chip" :class="{ 'tag-chip-muted': !plugin.enabled }">
                {{ plugin.enabled ? '已启用' : '已关闭' }}
              </span>
              <div class="plugin-status-note">{{ pluginHealthLabel(plugin) }}</div>
            </td>
            <td>
              <div class="toolbar compact plugin-row-actions">
                <SwitchField
                  :model-value="Boolean(plugin.enabled)"
                  @update:model-value="(enabled) => togglePlugin(plugin, enabled)"
                />
                <button class="btn btn-ghost" type="button" @click.stop="deletePlugin(plugin)">
                  删除
                </button>
                <button
                  v-if="updateFor(plugin)"
                  class="btn btn-primary"
                  type="button"
                  @click.stop="updatePlugin(plugin, updateFor(plugin))"
                >
                  更新
                </button>
              </div>
            </td>
          </tr>
          <tr v-if="!plugins.length">
            <td class="table-empty" colspan="4">当前没有已安装插件。</td>
          </tr>
        </tbody>
      </table>
    </article>

    <SectionCard v-if="!selectedPlugin" title="插件详情">
      <EmptyState text="请选择插件。" />
    </SectionCard>

    <template v-else>
      <InfoCard title="插件基本信息" :rows="pluginBasicRows(selectedPlugin)" />
      <InfoCard title="健康状态" :rows="pluginHealthRows(selectedPlugin)" />
      <TagListCard title="权限" :tags="pluginPermissionTags(selectedPlugin)" />
      <SimpleTableCard
        title="工具注册"
        :columns="['名称', '启用', '描述']"
        :rows="pluginToolRows(selectedPlugin)"
      />
      <SimpleTableCard
        title="中间件注册"
        :columns="['名称', '优先级', '启用', '描述']"
        :rows="pluginMiddlewareRows(selectedPlugin)"
      />

      <SectionCard title="插件配置">
        <EmptyState v-if="!configFields.length" text="该插件未注册配置项。" />
        <template v-else>
          <div class="plugin-form">
            <div v-for="field in configFields" :key="field.key" class="plugin-field field-card">
              <div class="field-card-head">
                <div class="field-card-title-wrap">
                  <div class="card-title">{{ field.label || field.key }}</div>
                  <div class="card-key">{{ field.key }}</div>
                </div>
              </div>

              <template v-if="field.type === 'bool'">
                <p v-if="field.description" class="card-help">{{ field.description }}</p>
                <SwitchField
                  :model-value="Boolean(draft[field.key])"
                  @update:model-value="(value) => setDraft(field.key, value)"
                />
              </template>

              <template v-else-if="isLlmConfigType(field.type)">
                <p class="card-help">{{ pluginLlmDescription(field) }}</p>
                <div class="llm-pick-row">
                  <code
                    class="llm-spec"
                    :class="{ 'llm-spec-unset': !pluginLlmHasValue(draft[field.key]) }"
                  >
                    {{ pluginLlmDisplayValue(draft[field.key]) }}
                  </code>
                  <button class="btn btn-secondary" type="button" @click="openLlmPicker(field)">
                    模型选择
                  </button>
                </div>
              </template>

              <template v-else-if="field.type === 'json'">
                <p v-if="field.description" class="card-help">{{ field.description }}</p>
                <textarea
                  class="textarea"
                  :title="field.description || undefined"
                  :value="pluginFieldText(draft[field.key])"
                  @input="setDraft(field.key, inputText($event))"
                ></textarea>
              </template>

              <template v-else-if="field.type === 'int' || field.type === 'float'">
                <p v-if="field.description" class="card-help">{{ field.description }}</p>
                <div v-if="numberSpec(field).type === 'range'">
                  <SliderField
                    :model-value="sliderValue(field)"
                    :min="numberSpec(field).min ?? 0"
                    :max="numberSpec(field).max ?? 1"
                    :step="numberSpec(field).step ?? 1"
                    :unit="numberSpec(field).unit || ''"
                    @update:model-value="(value) => setDraft(field.key, value)"
                  />
                </div>
                <input
                  v-else
                  class="number"
                  type="number"
                  :step="numberSpec(field).step ?? 1"
                  :title="field.description || undefined"
                  :value="draft[field.key] ?? ''"
                  @input="setDraft(field.key, inputText($event))"
                />
              </template>

              <template v-else>
                <p v-if="field.description" class="card-help">{{ field.description }}</p>
                <input
                  class="input"
                  :type="isSecretKey(field.key) ? 'password' : 'text'"
                  :title="field.description || undefined"
                  :value="
                    draft[field.key] === null || draft[field.key] === undefined
                      ? ''
                      : String(draft[field.key])
                  "
                  @input="setDraft(field.key, inputText($event))"
                />
              </template>
            </div>
          </div>
          <button class="btn btn-primary" type="button" @click="saveSelectedConfig">
            保存插件配置并重载
          </button>
        </template>
      </SectionCard>
    </template>
  </template>
</template>
