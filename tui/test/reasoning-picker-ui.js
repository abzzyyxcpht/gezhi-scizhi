// 思考强度档位（Ink reasoning-picker-ui 的 OpenTUI 移植）：
// REASONING_EFFORTS 全档位可设置、未知值拒绝、auto 与具名档位语义区分（控制器层）。
// 注：Ink 的交互式档位选择面板由 /reasoning 命令 + 模型配置面板（/models 思考档位）承接，
// 面板交互由 model-picker-ui 覆盖。
import assert from 'node:assert/strict';
import { createSessionController } from '../src/opentui/session-controller.js';
import { REASONING_EFFORTS } from '../src/research-commands.js';

function fakeBridge() {
  const calls = [];
  const settings = { llm_reasoning_effort: 'auto' };
  return {
    calls,
    settings,
    connection: () => ({ mode: 'local', url: '' }),
    call: async (request) => {
      calls.push(request);
      if (request.cmd === 'settings_get') return { ok: true, settings: { ...settings } };
      if (request.cmd === 'settings_save') {
        Object.assign(settings, request.settings);
        return { ok: true, settings: { ...settings } };
      }
      return { ok: true };
    },
  };
}

const textOf = (r) => (r?.lines || []).map(l => l.text).join('\n');
assert.ok(REASONING_EFFORTS.length >= 4, 'effort list has the full ladder');
assert.ok(REASONING_EFFORTS.includes('auto'), 'auto is a selectable value');

// 全档位逐一可设置，保存值精确回读
for (const effort of REASONING_EFFORTS) {
  const bridge = fakeBridge();
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit(`/reasoning ${effort}`);
  assert.ok(textOf(r).includes(`已设置为 ${effort}`), `effort ${effort} acknowledged`);
  const save = bridge.calls.find(c => c.cmd === 'settings_save');
  assert.equal(save?.settings?.llm_reasoning_effort, effort);
}

// 未识别值：拒绝且不保存
{
  const bridge = fakeBridge();
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning constructor');
  assert.ok(textOf(r).includes('用法：/reasoning'), 'unknown effort rejected');
  assert.ok(!bridge.calls.some(c => c.cmd === 'settings_save'));
}

// auto 与具名档位在读取层语义不同（auto 不冒充具名强度）
{
  const bridge = fakeBridge();
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const r = await ctrl.submit('/reasoning');
  assert.ok(textOf(r).includes('推理强度 auto'), 'auto reads back as auto');
}

console.log('Reasoning effort ladder: all levels settable, unknown rejected, auto semantics preserved');
process.exit(0);
