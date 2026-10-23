import assert from 'node:assert/strict';
import React from 'react';
import stringWidth from 'string-width';
import { testRender } from '@mbears/opentui-react/test-utils';
import { AppShell } from '../src/opentui/app-shell.js';

const status = '等待审批：请核对材料变更，确认后继续；当前任务仍然暂停';
for (const width of [28, 42, 80]) {
  let setup;
  try {
    await React.act(async () => { setup = await testRender(React.createElement(AppShell, { state: {
      terminalWidth: width, terminalHeight: 24, workspaceMode: 'research', reducedMotion: true,
      status, lines: [{ text: '成果验收：需要复核' }], draft: '保留输入', emptyArt: false,
      slotValues: { mode: '科研', cwd: '很长的科研项目文件夹'.repeat(4) },
      tips: [{ text: 'Ctrl+P 命令面板' }], appearance: { inspector_visible: false },
    } }), { width, height: 24 }); });
    await setup.renderOnce();
    const frame = setup.captureCharFrame();
    assert.ok(frame.split('\n').every(line => stringWidth(line) <= width), `${width}: shell must stay within terminal\n${frame}`);
    assert.ok(frame.replace(/\s/g, '').includes(status), `${width}: pending approval status must remain readable\n${frame}`);
    assert.ok(frame.includes('保留输入') && frame.includes('· 输入'), `${width}: composer stays reachable\n${frame}`);
  } finally { await React.act(async () => { setup?.renderer.destroy(); }); }
}
await new Promise(resolve => setTimeout(resolve, 150));
console.log('outcome-checks-layout: 28/42/80-column shell keeps approval status and composer visible');
process.exit(0);
