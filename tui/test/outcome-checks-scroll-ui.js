import assert from 'node:assert/strict';
import React from 'react';
import { testRender } from '@mbears/opentui-react/test-utils';
import { OpenTuiApp } from '../src/opentui/runtime.js';
import { createSessionController } from '../src/opentui/session-controller.js';
import { controllerBridge } from './outcome-checks-fixture.js';

for (const width of [28, 42, 80]) {
  const { bridge } = controllerBridge();
  const controller = createSessionController({ bridge });
  const submit = controller.submit;
  controller.submit = async text => text === '/fixture'
    ? { lines: Array.from({ length: 36 }, (_, index) => ({ text: index === 35 ? 'LAST-TRANSCRIPT' : `record ${index}` })) }
    : submit(text);
  let setup;
  const frame = () => setup.captureCharFrame().replace(/\s/g, '');
  const render = async () => React.act(async () => { await setup.renderOnce(); });
  const wait = async predicate => {
    for (let i = 0; i < 80; i++) { await render(); if (predicate()) return; await new Promise(resolve => setTimeout(resolve, 15)); }
    assert.fail(setup.captureCharFrame());
  };
  const command = async text => {
    await React.act(async () => { await setup.mockInput.typeText(text); });
    await render();
    await React.act(async () => { setup.mockInput.pressEnter(); });
    await render();
  };
  try {
    await React.act(async () => { setup = await testRender(React.createElement(OpenTuiApp,
      { controller, reducedMotion: true }), { width, height: 24 }); });
    await wait(() => frame().includes('输入'));
    await command('/fixture');
    await wait(() => frame().includes('LAST-TRANSCRIPT'));
    await command('/goal 等待审批材料核对后继续运行并保留所有科研记录与引用来源说明');
    await wait(() => frame().includes('目标已设置'));
    for (let i = 0; i < 30; i++) { await React.act(async () => { await setup.mockMouse.scroll(4, 8, 'up'); }); await render(); }
    assert.ok(frame().includes('格致SciZhi（OpenTUI引擎）'), `${width}: first transcript remains reachable\n${setup.captureCharFrame()}`);
    for (let i = 0; i < 30; i++) { await React.act(async () => { await setup.mockMouse.scroll(4, 8, 'down'); }); await render(); }
    assert.ok(frame().includes('LAST-TRANSCRIPT'), `${width}: scrolling returns to the latest result`);
    await React.act(async () => { await setup.mockInput.typeText('继续输入'); });
    await render();
    assert.ok(frame().includes('继续输入'), `${width}: input remains usable`);
  } finally { await React.act(async () => { setup?.renderer.destroy(); }); }
}
await new Promise(resolve => setTimeout(resolve, 150));
console.log('outcome-checks-scroll-ui: wrapped status preserves first/last transcript and input at 28/42/80 columns');
process.exit(0);
