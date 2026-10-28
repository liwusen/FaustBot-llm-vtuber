import { markRaw, reactive, ref, shallowRef } from 'vue';
import { defineStore } from 'pinia';
import type { Component } from 'vue';
import type { BannerType } from '@/config/types';

export interface ModalState {
  component: Component | null;
  props: Record<string, unknown>;
  title: string;
}

export const useUiStore = defineStore('configer-ui', () => {
  const bannerVisible = ref(false);
  const bannerType = ref<BannerType>('info');
  const bannerText = ref('');
  const busy = ref(false);

  const modalOpen = ref(false);
  const modalComponent = shallowRef<Component | null>(null);
  const modalProps = ref<Record<string, unknown>>({});
  const modalTitle = ref('');

  // 折叠状态按 key 记住；undefined 表示尚未操作，由组件决定默认值。
  const folds = reactive<Record<string, boolean>>({});
  const tableUIState = reactive<Record<string, { page: number; query: string }>>({});
  const scrollPositions = reactive<Record<string, number>>({});

  function showBanner(type: BannerType, text: string): void {
    bannerType.value = type;
    bannerText.value = text;
    bannerVisible.value = true;
  }

  function hideBanner(): void {
    bannerVisible.value = false;
  }

  function setBusy(value: boolean): void {
    busy.value = value;
  }

  function runModalDisposers(): void {
    if (!Array.isArray(window.__cfgModalDisposers)) return;
    while (window.__cfgModalDisposers.length) {
      const disposer = window.__cfgModalDisposers.pop();
      try {
        disposer?.();
      } catch {
        // 清理失败不应阻断关闭弹窗
      }
    }
  }

  function openModal(
    component: Component,
    props: Record<string, unknown> = {},
    title = '弹窗',
  ): void {
    runModalDisposers();
    modalComponent.value = markRaw(component);
    modalProps.value = props;
    modalTitle.value = title;
    modalOpen.value = true;
  }

  function closeModal(): void {
    runModalDisposers();
    modalOpen.value = false;
    modalComponent.value = null;
    modalProps.value = {};
    modalTitle.value = '';
  }

  function registerModalDisposer(fn: () => void): void {
    if (typeof fn !== 'function') return;
    if (!Array.isArray(window.__cfgModalDisposers)) window.__cfgModalDisposers = [];
    window.__cfgModalDisposers.push(fn);
  }

  function foldState(key: string): boolean | undefined {
    return folds[key];
  }

  function setFoldState(key: string, open: boolean): void {
    folds[key] = open;
  }

  function tableState(key: string): { page: number; query: string } {
    if (!tableUIState[key]) tableUIState[key] = { page: 1, query: '' };
    return tableUIState[key];
  }

  return {
    bannerVisible,
    bannerType,
    bannerText,
    busy,
    modalOpen,
    modalComponent,
    modalProps,
    modalTitle,
    folds,
    tableUIState,
    scrollPositions,
    showBanner,
    hideBanner,
    setBusy,
    openModal,
    closeModal,
    registerModalDisposer,
    runModalDisposers,
    foldState,
    setFoldState,
    tableState,
  };
});
