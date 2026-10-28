import { nextTick } from 'vue';
import { useModulesStore } from '@/stores/modules';

export interface OnboardingStep {
  title: string;
  body: string;
  target?: string;
  action?: () => void;
  actionLabel?: string;
  actionHint?: string;
}

function onboardingActionSwitch(moduleId: string): () => void {
  return () => {
    try {
      const modules = useModulesStore();
      if (modules.navigate(moduleId)) {
        void nextTick(() => {
          document.getElementById('cardsRoot')?.scrollTo({ top: 0 });
        });
      }
    } catch (err) {
      console.warn('switchModule failed', err);
    }
  };
}

function onboardingActionOpenStage(): void {
  if (window.api && typeof window.api.toggleWidgetEditMode === 'function') {
    window.api.toggleWidgetEditMode().catch((err: unknown) => {
      console.warn('toggleWidgetEditMode failed', err);
    });
  }
}

/** 文案与旧 constants.js 中的 CONFIG_ONBOARDING_STEPS 逐字一致。 */
export const CONFIG_ONBOARDING_STEPS: OnboardingStep[] = [
  {
    title: '欢迎使用配置中心',
    body: '这里是 FaustBot 的所有设置入口：模型、语音、记忆、插件都在这里配置。\n接下来带你认识核心功能，只需几步。',
  },
  {
    title: '配置模块',
    body: '左侧是全部配置模块：\nAI 服务商 · 模型 · 语音 · 角色 · 记忆 · Araya · 组件 · MCP · 触发器 · 技能 · 插件 · 高级\n点击模块即可切换到对应配置区。',
    target: '#moduleNav',
  },
  {
    title: 'AI 服务商',
    body: '选择对话模型、填写接口地址与密钥。',
    target: '[data-module="ai"]',
    action: onboardingActionSwitch('ai'),
    actionLabel: '切换到 AI 服务商',
    actionHint: '先「添加服务商」，再勾选主模型，改完记得点右上角保存。',
  },
  {
    title: '记忆',
    body: '知识库一体化管理：向量检索、图谱、文件都在这里。',
    target: '[data-module="memory"]',
    action: onboardingActionSwitch('memory'),
    actionLabel: '切换到记忆',
    actionHint: '这里是记忆模块，可以管理知识库与检索。',
  },
  {
    title: 'Araya',
    body: 'Araya 记忆库自动维护：空闲时自动整理记忆。',
    target: '[data-module="araya"]',
    action: onboardingActionSwitch('araya'),
    actionLabel: '切换到 Araya',
    actionHint: '这里是 Araya 模块，可以查看自动维护状态。',
  },
  {
    title: '插件',
    body: 'FaustBot 插件：安装、启用与管理扩展能力。',
    target: '[data-module="plugins"]',
    action: onboardingActionSwitch('plugins'),
    actionLabel: '切换到插件',
    actionHint: '这里是插件模块，可以浏览已安装插件。',
  },
  {
    title: '保存与应用',
    body: '修改配置后：\n保存 = 写入配置文件；应用 = 让后端立即生效。\n右上角会实时显示未保存的修改数。\n插件面板上的改动同样按这里的「保存」提交，不需要插件自己的保存按钮。',
    target: '#saveBtn',
  },
  {
    title: '调整 UI 布局',
    body: '主窗口的聊天栏、快捷控制器等位置都可以调整,右键组件打开菜单：\n点击下方按钮，会自动打开主窗口的布景台编辑模式，直接拖拽即可。\n 拖拽完成后按Esc退出编辑模式',
    action: onboardingActionOpenStage,
    actionLabel: '进入布景台',
    actionHint: '主窗口已进入布景台编辑模式，拖拽调整完,按Esc退出,然后,点击“下一步”继续。',
  },
  {
    title: '引导完成!',
    body: '配置中心的核心功能都介绍完了。\n祝你使用愉快！',
  },
];
