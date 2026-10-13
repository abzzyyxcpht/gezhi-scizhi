// Test-only preload: keep assertions intact while isolating restricted scratch paths.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { syncBuiltinESMExports } from 'node:module';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const restrictedRoot = path.join(root, '_testtmp') + path.sep;
const isolatedRoot = path.join(root, 'test-output', 'dual-mode');
const original = fs.mkdtempSync;
fs.mkdtempSync = (prefix, ...options) => {
  if (typeof prefix === 'string' && path.resolve(prefix).startsWith(restrictedRoot)) {
    fs.mkdirSync(isolatedRoot, { recursive: true });
    return original(path.join(isolatedRoot, `tui-${process.pid}-${path.basename(prefix)}`), ...options);
  }
  return original(prefix, ...options);
};
syncBuiltinESMExports();
