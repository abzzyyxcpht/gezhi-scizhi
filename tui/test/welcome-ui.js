// 空会话区响应式：六种尺寸不破版、双模式提示可发现、
// 不虚构文献篇数或进度。Ink 版测 Welcome 组件；OpenTUI 等价面是 AppShell 内的 EmptyTranscript
// （已加 clipToWidth 窄屏裁剪），这里经 AppShell 直装验证终端级边界。
// 窄尺寸下转写区随壳层 chrome 收缩，空状态按居中裁剪——身份与边界是渐进契约；
// 提示可发现性在标准尺寸（80×24 起）断言。
import assert from 'node:assert/strict';
import React from 'react';
import { testRender } from '@mbears/opentui-react/test-utils';
import stringWidth from 'string-width';
import { AppShell } from '../src/opentui/app-shell.js';

async function renderShell(width, height, workspaceMode = 'general') {
  const setup = await testRender(
    React.createElement(AppShell, { state: { workspaceMode, terminalWidth: width, terminalHeight: height } }),
    { width, height, targetFps: 30 },
  );
  for (let i = 0; i < 5; i++) await setup.renderOnce();
  const frame = setup.captureCharFrame();
  setup.renderer.destroy();
  return frame;
}

const invented = /\d+\s*篇|\d+\s*\/\s*\d+\s*节点/u;
const sizes = [[114, 16], [74, 12], [50, 8], [32, 6], [18, 4], [10, 2]];
for (const [width, height] of sizes) {
  const frame = await renderShell(width, height);
  const rows = frame.replace(/\n$/, '').split('\n');
  assert.ok(rows.length <= height, `${width}x${height}: exceeds transcript height`);
  assert.ok(rows.every(line => stringWidth(line) <= width), `${width}x${height}: exceeds transcript width`);
  assert.ok(!invented.test(frame), `${width}x${height}: welcome never invents paper counts or progress`);
  if (width >= 18) {
    assert.ok(frame.includes('格致'), `${width}x${height}: identity survives compact layouts`);
  } else {
    // 10×2：转写区为 0 行，壳层让位给 composer——输入仍可达（渐进降级，不白屏）
    assert.ok(frame.includes('❯'), '10x2: composer stays reachable when no transcript area remains');
  }
}

const general = await renderShell(80, 24);
assert.ok(general.includes('格 致 · 通 用'), 'general empty art uses the open pattern');
assert.ok(general.includes('输入消息开始'), 'empty art invites input');
assert.ok(general.includes('/ 触发命令补全'), 'general hints are discoverable');
assert.ok(general.includes('· 输入'), 'composer remains accessible');
assert.ok(!invented.test(general), 'general welcome never invents paper counts or progress');

const research = await renderShell(80, 24, 'research');
assert.ok(research.includes('格 致 · 科 研'), 'research empty art uses the focused pattern');
assert.ok(research.includes('/steps 选择科研步骤'), 'research hints are discoverable');
assert.ok(research.includes('◎'), 'research mode badge');
assert.ok(!invented.test(research), 'research welcome never invents paper counts or progress');
console.log(`Responsive SciZhi empty transcript: ${sizes.length} OpenTUI shell sizes and dual-mode hints passed`);
