// Capture the real App/Ink inspector with the same isolated fixtures as the tests.
import fs from 'node:fs';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { captureContextInspectorFrames } from './context-inspector-ui.js';
import { captureSkillModesFrames } from './skills-modes-ui.js';

const output = path.resolve(path.dirname(fileURLToPath(import.meta.url)), '../../design-previews/2026-10-06-context-integrity');
fs.mkdirSync(output, { recursive: true });
const context = await captureContextInspectorFrames();
const skills = await captureSkillModesFrames();
const frames = [...context.frames, ...skills.frames];
for (const frame of frames) {
  fs.writeFileSync(path.join(output, `tui-${frame.id}.txt`),
    `实际 App / Ink 渲染；内存合成样例；无模型请求。\n终端 ${frame.width}×${frame.height}\n\n${frame.text}\n`, 'utf8');
}
fs.writeFileSync(path.join(output, 'tui-manifest.json'), JSON.stringify({
  fixture: true, source: context.source, native_pty: false, assertions_passed: true,
  no_model_request: true, captures: frames.map(({ text, ...frame }) => ({ ...frame, file: `tui-${frame.id}.txt` })),
}, null, 2) + '\n', 'utf8');
console.log(`Saved ${frames.length} actual Ink frames: ${output}`);
