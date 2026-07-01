#!/usr/bin/env node
/**
 * Check JavaScript syntax for all frontend JS files.
 * Uses Node's built-in parser (no execution).
 * Usage: node scripts/check-js-syntax.js
 */
const fs = require('fs');
const path = require('path');
const { execSync } = require('child_process');

const FRONTEND_DIR = path.resolve(__dirname, '..');
const SKIP_DIRS = new Set(['node_modules', 'dist', '.git']);
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
    try {
      execSync(`node -c "${full}"`, { stdio: 'pipe', timeout: 5000 });
      console.log(`  OK  ${path.relative(FRONTEND_DIR, full)}`);
    } catch {
      console.error(`FAIL  ${path.relative(FRONTEND_DIR, full)}`);
      try {
        // re-run for readable stderr
        execSync(`node -c "${full}"`, { stdio: 'inherit', timeout: 5000 });
      } catch { /* already printed */ }
      errors += 1;
    }
  }
}

console.log('Checking JavaScript syntax...\n');
walk(FRONTEND_DIR);
console.log(`\n${errors === 0 ? 'All files OK.' : `${errors} file(s) have syntax errors.`}`);
process.exit(errors === 0 ? 0 : 1);
