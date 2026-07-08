let _monacoLoadPromise = null;

function ensureMonacoLoaded() {
  if (window.monaco && window.monaco.editor) {
    return Promise.resolve(window.monaco);
  }
  if (_monacoLoadPromise) return _monacoLoadPromise;

  _monacoLoadPromise = new Promise((resolve, reject) => {
    const finish = () => {
      try {
        window.require.config({ paths: { vs: './libs/monaco/vs' } });
        window.require(['vs/editor/editor.main'], () => resolve(window.monaco), reject);
      } catch (err) {
        reject(err);
      }
    };

    if (window.require && window.require.config) {
      finish();
      return;
    }

    const script = document.createElement('script');
    script.src = './libs/monaco/vs/loader.js';
    script.onload = finish;
    script.onerror = () => reject(new Error('Monaco loader 加载失败'));
    document.head.appendChild(script);
  });

  return _monacoLoadPromise;
}

function registerModalDisposer(fn) {
  if (typeof fn !== 'function') return;
  if (!Array.isArray(window.__cfgModalDisposers)) {
    window.__cfgModalDisposers = [];
  }
  window.__cfgModalDisposers.push(fn);
}

function guessMonacoLanguage(filename) {
  const name = String(filename || '').toLowerCase();
  if (name.endsWith('.md')) return 'markdown';
  if (name.endsWith('.json')) return 'json';
  if (name.endsWith('.js')) return 'javascript';
  if (name.endsWith('.ts')) return 'typescript';
  if (name.endsWith('.py')) return 'python';
  if (name.endsWith('.html')) return 'html';
  if (name.endsWith('.css')) return 'css';
  return 'plaintext';
}

async function createMonacoEditorHost(container, value, options) {
  const monaco = await ensureMonacoLoaded();
  const editor = monaco.editor.create(container, {
    value: String(value || ''),
    language: options && options.language ? options.language : 'plaintext',
    readOnly: !!(options && options.readOnly),
    automaticLayout: true,
    minimap: { enabled: false },
    scrollBeyondLastLine: false,
    wordWrap: 'on',
    theme: 'vs',
    fontSize: 13,
    lineNumbersMinChars: 3,
  });
  registerModalDisposer(() => editor.dispose());
  return editor;
}