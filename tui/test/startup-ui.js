// 启动屏（Ink startup-ui 的 OpenTUI 移植，组件级）：诚实连接态、失败显式可重试、
// 多姿态采样、身份常在、80x24 有界；连接期间输入可编辑属 runtime 行为（夹具层已覆盖）。
import assert from 'node:assert/strict';
import React from 'react';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import stringWidth from 'string-width';
import { testRender } from '@mbears/opentui-react/test-utils';
import { StartupScreen } from '../src/opentui/components/startup-screen.js';
import { revealStartupArt } from '../src/opentui/motion/frames.js';

const pause = ms => new Promise(resolve => setTimeout(resolve, ms));
process.on('unhandledRejection', (error) => { console.error(error); process.exit(1); });

async function renderStartup(props) {
  const setup = await testRender(React.createElement(StartupScreen, props), { width: 80, height: 24, targetFps: 30 });
  await setup.renderOnce();
  await pause(30);
  await setup.renderOnce();
  return setup;
}

function checkFrame(frame, width, height, label) {
  const rows = frame.replace(/\n$/, '').split('\n');
  assert.ok(rows.length <= height, `${label}: exceeds ${height} rows\n${frame}`);
  assert.ok(rows.every(line => stringWidth(line) <= width), `${label}: exceeds ${width} columns\n${frame}`);
}

// 1) reducedMotion：静态格印 + 诚实连接态
{
  const setup = await renderStartup({
    workspaceMode: 'general', spinnerFrame: 0, shimmerFrame: 0, error: '', reducedMotion: true,
  });
  try {
    const frame = setup.captureCharFrame();
    assert.ok(frame.includes('格'), 'identity present');
    assert.ok(frame.includes('连接科研引擎'), 'honest connecting state');
    checkFrame(frame, 80, 24, 'reduced static');
  } finally { await setup.cleanup?.(); }
}

// 2) 失败态：显式错误 + 重试指引，不伪装连接
{
  const setup = await renderStartup({
    workspaceMode: 'general', spinnerFrame: 0, shimmerFrame: 24, error: 'fixture engine unavailable', reducedMotion: true,
  });
  try {
    const frame = setup.captureCharFrame();
    assert.ok(frame.includes('fixture engine unavailable'), 'failure is explicit');
    assert.ok(frame.includes('重试'), 'retry guidance present');
    checkFrame(frame, 80, 24, 'failure');
  } finally { await setup.cleanup?.(); }
}

// 3) 动效：采样姿态随帧推进（不依赖真实时间）
{
  const poses = [0, 6, 12].map(frame => revealStartupArt('general', frame));
  assert.ok(new Set(poses.map(p => p.join('|'))).size > 1, 'sampling arc advances across frames');
  const setup = await renderStartup({
    workspaceMode: 'research', spinnerFrame: 2, shimmerFrame: 0, error: '', reducedMotion: false,
  });
  try {
    const frame = setup.captureCharFrame();
    assert.ok(frame.includes('✦'), 'research focused-grid art on startup screen');
    checkFrame(frame, 80, 24, 'animated');
  } finally { await setup.cleanup?.(); }
}

console.log('Startup screen: honest states, explicit failure, bounded 80x24 and sampled poses passed');
process.exit(0);
