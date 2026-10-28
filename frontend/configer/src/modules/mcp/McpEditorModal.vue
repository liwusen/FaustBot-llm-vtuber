<script setup lang="ts">
import { computed, ref } from 'vue';
import { useUiStore } from '@/stores/ui';
import {
  buildMcpPayload,
  mcpArgsToText,
  mcpEditorBody,
  mcpHeadersToText,
  type McpPayload,
  type McpServerRecord,
  type McpTransport,
} from './mcpUtils';

const props = defineProps<{
  initial: McpServerRecord | null;
  onSubmit: (payload: McpPayload) => Promise<void> | void;
}>();

const ui = useUiStore();

const body = mcpEditorBody(props.initial);
const editing = Boolean(props.initial);

const serverId = ref(body.server_id);
const enabled = ref(body.enabled);
const description = ref(body.description);
const transport = ref<McpTransport>(body.transport);
const custom = ref(body.custom);
const command = ref(body.command);
const argsText = ref(mcpArgsToText(body.args));
const url = ref(body.url);
const headersText = ref(mcpHeadersToText(body.headers));

const isHttpLike = computed(() => transport.value !== 'stdio');
const urlLabel = computed(() => (
  transport.value === 'sse' ? 'SSE URL' : 'Streamable HTTP URL'
));
const commandDisabled = computed(() => isHttpLike.value || !custom.value);

async function save(): Promise<void> {
  const payload = buildMcpPayload({
    server_id: serverId.value,
    enabled: enabled.value,
    description: description.value,
    transport: transport.value,
    custom: custom.value,
    command: command.value,
    argsText: argsText.value,
    url: url.value,
    headersText: headersText.value,
  });
  if (!payload.server_id) {
    window.alert('服务器标识不能为空');
    return;
  }
  await props.onSubmit(payload);
  ui.closeModal();
}
</script>

<template>
  <div class="form-grid">
    <div class="form-field">
      <label class="form-field-label">服务器标识</label>
      <p class="form-hint">
        {{ editing
          ? '服务器的唯一 ID，创建后不可修改。'
          : '服务器的唯一 ID，用于区分不同 MCP 服务，创建后不可修改。建议使用小写字母与连字符。' }}
      </p>
      <input
        v-model="serverId"
        class="input"
        type="text"
        :disabled="editing"
        placeholder="例如 filesystem"
      />
    </div>

    <div class="form-field">
      <label class="form-field-label">启用</label>
      <p class="form-hint">关闭后该服务器不会启动，其提供的工具对 Agent 不可见。</p>
      <label class="switch-row" style="cursor: pointer">
        <input v-model="enabled" type="checkbox" />
        <span class="switch-text">{{ enabled ? '已启用' : '已停用' }}</span>
      </label>
    </div>

    <div class="form-field">
      <label class="form-field-label">描述</label>
      <p class="form-hint">对该 MCP 服务的备注说明，仅用于在列表中辨识，不影响运行。</p>
      <input
        v-model="description"
        class="input"
        type="text"
        placeholder="简要说明该服务器提供的能力"
      />
    </div>

    <div class="form-field">
      <label class="form-field-label">传输模式</label>
      <p class="form-hint">stdio 通过本地子进程通信；sse / streamable-http 连接远程 HTTP 服务。选择后下方字段会自动切换。</p>
      <div class="form-segment">
        <label>
          <input v-model="transport" type="radio" name="mcpTransport" value="stdio" />
          <span>stdio（本地进程）</span>
        </label>
        <label>
          <input v-model="transport" type="radio" name="mcpTransport" value="sse" />
          <span>sse（远程）</span>
        </label>
        <label>
          <input v-model="transport" type="radio" name="mcpTransport" value="streamable-http" />
          <span>streamable-http（远程）</span>
        </label>
      </div>
    </div>

    <div v-show="!isHttpLike" class="form-grid">
      <div class="form-field">
        <label class="form-field-label">自定义 stdio server</label>
        <p class="form-hint">开启后可自定义启动命令与参数；关闭时使用内置命令，命令字段将被锁定。</p>
        <label class="switch-row" style="cursor: pointer">
          <input v-model="custom" type="checkbox" />
          <span class="switch-text">{{ custom ? '自定义命令' : '使用内置命令' }}</span>
        </label>
      </div>

      <div class="form-field">
        <label class="form-field-label">命令</label>
        <p class="form-hint">启动 MCP server 的可执行程序，需在系统 PATH 中或使用绝对路径。</p>
        <input
          v-model="command"
          class="input"
          type="text"
          :disabled="commandDisabled"
          placeholder="例如 node、python、npx"
        />
      </div>

      <div class="form-field">
        <label class="form-field-label">参数</label>
        <p class="form-hint">传递给命令的启动参数，每行一个，按顺序拼接。</p>
        <textarea
          v-model="argsText"
          class="textarea"
          placeholder="@modelcontextprotocol/server-filesystem&#10;/path/to/dir"
        ></textarea>
      </div>
    </div>

    <div v-show="isHttpLike" class="form-grid">
      <div class="form-field">
        <label class="form-field-label">{{ urlLabel }}</label>
        <p class="form-hint">远程 MCP 服务的完整访问地址（含协议与路径）。</p>
        <input v-model="url" class="input" type="text" placeholder="https://example.com/mcp" />
      </div>

      <div class="form-field">
        <label class="form-field-label">HTTP Headers</label>
        <p class="form-hint">连接远程服务时附带的请求头，每行一条，格式为 key: value（如鉴权 Token）。</p>
        <textarea
          v-model="headersText"
          class="textarea"
          placeholder="Authorization: Bearer xxxxx"
        ></textarea>
      </div>
    </div>
  </div>

  <div class="toolbar">
    <button class="btn btn-primary" type="button" @click="save">保存</button>
    <button class="btn btn-ghost" type="button" @click="ui.closeModal()">取消</button>
  </div>
</template>
