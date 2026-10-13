import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureWorkspaceModeFrames } from '../test/workspace-mode-ui.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const destination = path.join(root, 'design-previews', '2026-10-04-dual-mode', 'tui.json');
const result = await captureWorkspaceModeFrames();
fs.mkdirSync(path.dirname(destination), { recursive: true });
fs.writeFileSync(destination, JSON.stringify(result, null, 2) + '\n', 'utf8');
console.log(`${result.frames.length} verified App/Ink frames saved to ${destination}`);
