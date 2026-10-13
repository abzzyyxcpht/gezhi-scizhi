// Actual App/Ink frames, using the same isolated fixtures as interaction QA.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureCommandPaletteFrames } from '../test/command-palette-ui.js';
import { captureModelPickerFrames } from '../test/model-picker-ui.js';
import { captureReasoningAppFrames } from '../test/reasoning-app-ui.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'design-previews/2026-10-03-comprehensive-upgrade/tui');
fs.mkdirSync(output, { recursive: true });
const commands = await captureCommandPaletteFrames();
const models = await captureModelPickerFrames();
const transitions = await captureReasoningAppFrames();
for (const [group, report] of Object.entries({ commands, models, transitions })) {
  fs.writeFileSync(path.join(output, `${group}.json`), JSON.stringify(report, null, 2), 'utf8');
  for (const frame of report.frames) fs.writeFileSync(path.join(output, `${group}-${frame.id}.txt`), frame.text, 'utf8');
}
console.log(JSON.stringify({ output, frames: commands.frames.length + models.frames.length + transitions.frames.length,
  fixture: true, native_pty: false, assertions_passed: true }));
