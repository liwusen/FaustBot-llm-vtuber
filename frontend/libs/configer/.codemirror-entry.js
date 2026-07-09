// Entry point for esbuild — bundles CodeMirror 6 into a single IIFE
// Used by: npm run build:bundle
// Output: libs/codemirror/codemirror-bundle.js
import { EditorView, keymap, lineNumbers, highlightActiveLine, highlightActiveLineGutter } from '@codemirror/view';
import { EditorState } from '@codemirror/state';
import { defaultKeymap, historyKeymap, history, redo, undo } from '@codemirror/commands';
import { searchKeymap } from '@codemirror/search';
import { markdown } from '@codemirror/lang-markdown';
import { json } from '@codemirror/lang-json';
import { syntaxHighlighting, defaultHighlightStyle } from '@codemirror/language';

window.CodeMirror = {
  EditorView,
  keymap,
  lineNumbers,
  highlightActiveLine,
  highlightActiveLineGutter,
  EditorState,
  defaultKeymap,
  historyKeymap,
  history,
  redo,
  undo,
  searchKeymap,
  markdown,
  json,
  syntaxHighlighting,
  defaultHighlightStyle,
};
