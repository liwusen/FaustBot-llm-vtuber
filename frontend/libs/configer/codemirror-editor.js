// CodeMirror 6 编辑器 — 使用本地 esbuild 打包的 bundle（codemirror-bundle.js）
// 在 config-window.html 中通过 <script src> 加载，暴露 window.CodeMirror 全局变量
// 暴露兼容的 createCodeMirrorEditor / guessEditorLanguage

var cmEditorMode = null; // "local" | "builtin" | void

function guessEditorLanguage(filename) {
  var name = String(filename || '').toLowerCase();
  if (name.endsWith('.md')) return 'markdown';
  if (name.endsWith('.json')) return 'json';
  return 'markdown';
}

function createCodeMirrorEditor(container, value, options) {
  var cm = window.CodeMirror;
  if (!cm) {
    console.error('[CodeMirror] window.CodeMirror not available — bundle not loaded');
    container.textContent = 'CodeMirror 加载失败，请刷新页面重试。';
    return null;
  }
  cmEditorMode = 'local';

  var language = (options && options.language) ? options.language : 'markdown';

  var langExt;
  if (language === 'json') {
    langExt = cm.json();
  } else {
    langExt = cm.markdown();
  }

  var readOnly = options && options.readOnly
    ? cm.EditorView.editable.of(false)
    : [];

  container.textContent = '';
  container.classList.add('cm-host');

  var state = cm.EditorState.create({
    doc: String(value || ''),
    extensions: [
      cm.EditorView.lineWrapping,
      cm.lineNumbers(),
      cm.highlightActiveLine(),
      cm.history(),
      cm.syntaxHighlighting(cm.defaultHighlightStyle, { fallback: true }),
      cm.EditorView.theme({
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
      cm.keymap.of([
        ...cm.defaultKeymap,
        ...cm.searchKeymap,
      ]),
      langExt,
      ...(Array.isArray(readOnly) ? readOnly : []),
    ],
  });

  var view = new cm.EditorView({
    state: state,
    parent: container,
  });

  view.dom.style.height = '100%';
  view.dom.style.width = '100%';

  // 附加 getValue 方法
  view.getValue = function () { return view.state.doc.toString(); };

  return view;
}

// 暴露全局 API
window.createCodeMirrorEditor = createCodeMirrorEditor;
window.guessEditorLanguage = guessEditorLanguage;
window.cmCreateEditor = createCodeMirrorEditor;
window.cmGuessLanguage = guessEditorLanguage;
