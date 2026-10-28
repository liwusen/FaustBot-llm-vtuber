import { defaultKeymap, history, historyKeymap } from '@codemirror/commands';
import { json } from '@codemirror/lang-json';
import { markdown } from '@codemirror/lang-markdown';
import { defaultHighlightStyle, syntaxHighlighting } from '@codemirror/language';
import { searchKeymap } from '@codemirror/search';
import { EditorState } from '@codemirror/state';
import {
  EditorView,
  highlightActiveLine,
  keymap,
  lineNumbers,
} from '@codemirror/view';

export type CodeMirrorEditor = EditorView & { getValue(): string };

export function guessEditorLanguage(filename: string): string {
  const name = String(filename || '').toLowerCase();
  if (name.endsWith('.json')) return 'json';
  return 'markdown';
}

export function createCodeMirrorEditor(
  container: HTMLElement,
  value: string,
  options?: { language?: string; readOnly?: boolean; onChange?: (value: string) => void },
): EditorView | null {
  const language = options?.language || 'markdown';
  const languageExtension = language === 'json' ? json() : markdown();
  const readOnly = options?.readOnly ? EditorView.editable.of(false) : [];
  const onChange = typeof options?.onChange === 'function' ? options.onChange : null;

  container.textContent = '';
  container.classList.add('cm-host');

  const state = EditorState.create({
    doc: String(value || ''),
    extensions: [
      EditorView.lineWrapping,
      lineNumbers(),
      highlightActiveLine(),
      history(),
      syntaxHighlighting(defaultHighlightStyle, { fallback: true }),
      EditorView.theme({
        '&': {
          fontSize: '14px',
          height: '100%',
        },
        '.cm-scroller': {
          fontFamily: "'Consolas', 'Courier New', monospace",
          minHeight: '100%',
        },
        '.cm-content': {
          minHeight: '100%',
        },
        '.cm-activeLine': {
          backgroundColor: 'rgba(0, 0, 0, 0.05)',
        },
        '.cm-activeLineGutter': {
          backgroundColor: 'rgba(0, 0, 0, 0.08)',
        },
        '&.cm-focused .cm-activeLine': {
          backgroundColor: 'rgba(0, 120, 255, 0.08)',
        },
        '&.cm-focused .cm-activeLineGutter': {
          backgroundColor: 'rgba(0, 120, 255, 0.12)',
        },
      }),
      keymap.of([...defaultKeymap, ...searchKeymap, ...historyKeymap]),
      languageExtension,
      ...(Array.isArray(readOnly) ? readOnly : []),
      ...(onChange
        ? [
            EditorView.updateListener.of((update) => {
              if (update.docChanged) onChange(update.state.doc.toString());
            }),
          ]
        : []),
    ],
  });

  const view = new EditorView({ state, parent: container }) as CodeMirrorEditor;
  view.dom.style.height = '100%';
  view.dom.style.width = '100%';
  view.getValue = () => view.state.doc.toString();

  return view;
}

window.createCodeMirrorEditor = createCodeMirrorEditor;
window.guessEditorLanguage = guessEditorLanguage;
window.cmCreateEditor = createCodeMirrorEditor;
window.cmGuessLanguage = guessEditorLanguage;
