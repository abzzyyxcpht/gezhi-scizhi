// Replayable evidence from the real App; the backend is an isolated fixture.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureReasoningPickerFrames } from '../test/reasoning-picker-ui.js';
import { captureReasoningAppFrames } from '../test/reasoning-app-ui.js';

const root = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../..');
const output = path.join(root, 'design-previews/2026-10-03-agent-polish/tui');
fs.mkdirSync(output, { recursive: true });
const picker = await captureReasoningPickerFrames();
const transitions = await captureReasoningAppFrames();
for (const [name, result] of Object.entries({ picker, transitions })) {
  fs.writeFileSync(path.join(output, `${name}.json`), JSON.stringify(result, null, 2), 'utf8');
  for (const frame of result.frames) fs.writeFileSync(path.join(output, `${name}-${frame.id}.txt`), frame.text, 'utf8');
}
console.log(JSON.stringify({ output, picker_frames: picker.frames.length,
  transition_frames: transitions.frames.length, native_pty: false, assertions_passed: true }));
