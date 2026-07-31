// Entry point for esbuild — bundles marked + DOMPurify + mermaid into a single IIFE
// Used by: npm run build:markdown
// Output: libs/markdown/markdown-bundle.js
import { marked } from 'marked';
import DOMPurify from 'dompurify';
import mermaid from 'mermaid';

window.FaustMarkdown = {
  marked,
  DOMPurify,
  mermaid,
};
