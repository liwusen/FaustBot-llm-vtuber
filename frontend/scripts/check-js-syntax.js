#!/usr/bin/env node
/**
 * Check JavaScript syntax for all frontend JS files.
 * Parses in-process with vm.Script (classic script); files detected as ESM
 * are checked via `node --check` on a temp .mjs copy (module goal).
 * Usage: node scripts/check-js-syntax.js
 */
const fs = require('fs');
const os = require('os');
const path = require('path');
const vm = require('vm');
const { execFileSync, spawnSync } = require('child_process');

// vm.SourceTextModule (ESM parse) needs --experimental-vm-modules; re-exec self
// once so the checker never has to remember the flag.
if (!vm.SourceTextModule && !process.env.FAUST_SYNTAX_INNER) {
  const r = spawnSync(process.execPath, ['--experimental-vm-modules', __filename, ...process.argv.slice(2)], {
    stdio: 'inherit',
    env: { ...process.env, FAUST_SYNTAX_INNER: '1' },
  });
  process.exit(r.status ?? 1);
}

const FRONTEND_DIR = path.resolve(__dirname, '..');
const SKIP_DIRS = new Set(['node_modules', 'dist', '.git']);
const SKIP_FILES = new Set([
  '.codemirror-entry.js',
  '.echarts-entry.js',
  '.pixi-live2d-entry.js',
  '.soullink-entry.js',
  '.profile-generator-entry.js',
  '.markdown-entry.js',
]);
let errors = 0;

function walk(dir) {
  const entries = fs.readdirSync(dir, { withFileTypes: true });
  for (const e of entries) {
    const full = path.join(dir, e.name);
    if (e.isDirectory()) {
      if (!SKIP_DIRS.has(e.name)) walk(full);
      continue;
    }
    if (!e.name.endsWith('.js')) continue;
    if (SKIP_FILES.has(e.name)) continue;
    checkFile(full);
  }
}

function checkFile(full) {
  const rel = path.relative(FRONTEND_DIR, full);
  let src;
  try {
    src = fs.readFileSync(full, 'utf8');
  } catch (e) {
    console.error(`FAIL  ${rel}  (read failed: ${e.message})`);
    errors += 1;
    return;
  }

  // Fast path: classic script parse, no process spawn.
  let classicErr = null;
  try {
    new vm.Script(src, { filename: full });
    console.log(`  OK  ${rel}`);
    return;
  } catch (e) {
    classicErr = e;
  }

  // ESM path: in-process module-goal parse when available, else node --check
  // on a temp .mjs copy (extension determines module goal).
  try {
    if (vm.SourceTextModule) {
      new vm.SourceTextModule(src, { identifier: full });
    } else {
      const tmp = path.join(os.tmpdir(), `faust-syntax-${process.pid}-${Date.now()}.mjs`);
      try {
        fs.writeFileSync(tmp, src);
        execFileSync(process.execPath, ['--check', tmp], { stdio: 'pipe' });
      } finally {
        try { fs.unlinkSync(tmp); } catch { /* best effort */ }
      }
    }
    console.log(`  OK  ${rel} (module)`);
  } catch (e) {
    fail(rel, `classic: ${classicErr.message.trim()}\n      module: ${String(e.message || e).trim().split('\n').slice(0, 3).join('\n      ')}`);
  }
}

function fail(rel, err) {
  console.error(`FAIL  ${rel}\n      ${String(err).trim().split('\n').join('\n      ')}`);
  errors += 1;
}

console.log('Checking JavaScript syntax...\n');
walk(FRONTEND_DIR);
console.log(`\n${errors === 0 ? 'All files OK.' : `${errors} file(s) have syntax errors.`}`);
process.exit(errors === 0 ? 0 : 1);
