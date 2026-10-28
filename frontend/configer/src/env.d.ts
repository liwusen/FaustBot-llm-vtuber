/// <reference types="vite/client" />

import type { EditorView } from '@codemirror/view';
import type { FrontendApi } from '@/api/types';
import type { PluginUI } from '@/plugin/types';

export {};

export interface TtsSplitterApi {
  tokenLength(text: string): number;
  extractCompletedSentences(buffer: string): {
    completed: Array<{ text: string; start: number; end: number }>;
    rest: string;
  };
  createChunker(idealTokens: number): {
    feed(delta: string): Array<{ text: string; start: number; end: number }>;
    flush(): Array<{ text: string; start: number; end: number }>;
  };
  demoSplit(text: string, idealTokens: number): string[];
}

declare global {
  interface Window {
    api?: FrontendApi;
    pluginUI: PluginUI;
    echarts?: typeof import('echarts');
    CodeMirror?: Record<string, unknown>;
    createCodeMirrorEditor?: (
      container: HTMLElement,
      value: string,
      options?: { language?: string; readOnly?: boolean; onChange?: (value: string) => void },
    ) => EditorView | null;
    guessEditorLanguage?: (filename: string) => string;
    cmCreateEditor?: Window['createCodeMirrorEditor'];
    cmGuessLanguage?: Window['guessEditorLanguage'];
    TtsSplitter?: TtsSplitterApi;
    FaustIcons?: {
      svg(name: string, opts?: { size?: number; className?: string }): string;
      mount(): void;
      names: string[];
    };
    configAppUI?: {
      startConfigOnboarding?: () => void;
    };
    deeplink?: {
      onConfigFaustCloud(cb: (payload: unknown) => void): void;
    };
    faust?: {
      onCommand(cb: (cmd: string) => void): void;
      onPluginInstallResult(cb: (payload: unknown) => void): void;
      onToggleLogPanel(cb: () => void): void;
    };
    logToMain?: {
      info(msg: string): Promise<unknown>;
    };
    __cfgModalDisposers?: Array<() => void>;
  }
}
