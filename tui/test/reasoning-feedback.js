// 思考强度反馈（Ink reasoning-feedback 的 OpenTUI 等价，纯控制器层）：
// 读取不伪装保存回执、保存确认、未知强度如实拒绝、远程不发送配置。
import assert from 'node:assert/strict';
import { createSessionController } from '../src/opentui/session-controller.js';

function fakeBridge({ mode = 'local', settings = {}, failSave = false } = {}) {
  const calls = [];
  return {
    calls,
    connection: () => ({ mode, url: mode === 'remote' ? 'http://fixture' : '' }),
    call: async (request) => {
      calls.push(request);
      if (request.cmd === 'settings_get') return { ok: true, settings: { llm_reasoning_effort: 'low', ...settings } };
      if (request.cmd === 'settings_save') {
        if (failSave) return { ok: false, error: 'fixture：设置保存失败' };
        Object.assign(settings, request.settings);
        return { ok: true, settings: { ...settings } };
      }
      return { ok: true };
    },
  };
}

const textOf = (r) => (r?.lines || []).map(l => l.text).join('\n');

// 1) 读取当前值：显示推理强度，但不产生"已保存"回执
{
  const bridge = fakeBridge();
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning');
  assert.ok(textOf(r).includes('推理强度 low'), 'current value visible');
  assert.ok(!textOf(r).includes('已保存'), 'reading never acknowledges a save');
  assert.ok(!bridge.calls.some(c => c.cmd === 'settings_save'), 'read path does not save');
}

// 2) 合法值保存：settings_save 收到该值并确认
{
  const bridge = fakeBridge();
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning high');
  const save = bridge.calls.find(c => c.cmd === 'settings_save');
  assert.deepEqual(save?.settings, { llm_reasoning_effort: 'high' });
  assert.ok(textOf(r).includes('已设置为 high'), 'save acknowledged');
}

// 3) 未识别强度：如实拒绝，不发明档位
{
  const bridge = fakeBridge();
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning constructor');
  assert.ok(textOf(r).includes('用法：/reasoning'), 'unknown intensity rejected');
  assert.ok(!bridge.calls.some(c => c.cmd === 'settings_save'));
}

// 4) 保存失败：返回错误，不伪装成功
{
  const bridge = fakeBridge({ failSave: true });
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning low');
  assert.ok(textOf(r).includes('设置保存失败'), 'failed save surfaced');
}

// 5) 远程模式：不发送配置或密钥（主机专属操作）
{
  const bridge = fakeBridge({ mode: 'remote' });
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning high');
  assert.ok(textOf(r).includes('主机'), 'remote routes to host');
  assert.ok(!bridge.calls.some(c => c.cmd === 'settings_save'), 'remote never saves locally');
}

console.log('Reasoning feedback controller: read-only display, explicit save, honest rejection, failure and remote isolation passed');
process.exit(0);
