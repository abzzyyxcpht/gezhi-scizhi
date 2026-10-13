// 配置面板开关组（OpenTUI）：实验能力开关在 /config 面板可见可切换，
// configItems 从 settings 读取、configSet 走 settings_save（对齐 ZCode 设置开关族）。
import assert from 'node:assert/strict';
import { CONFIG_GROUPS, flattenConfig } from '../src/opentui/components/config-panel.js';
import { createSessionController } from '../src/opentui/session-controller.js';

// 1) 面板注册表：实验能力组在位，键与活模块（queue/review-commands）一致
const experimentGroup = CONFIG_GROUPS.find((g) => g.title === '实验能力');
assert.ok(experimentGroup, '实验能力分组存在');
const keys = experimentGroup.items.map((i) => i.key);
assert.ok(keys.includes('experimental_message_queue'), '下一轮队列开关在位');
assert.ok(keys.includes('experimental_evidence_reviews'), '后台证据复核开关在位');
assert.ok(experimentGroup.items.every((i) => i.type === 'bool'), '实验项均为开关型');

// 2) configItems：settings 布尔映射到面板值（缺省 false）
{
  const calls = [];
  const bridge = {
    connection: () => ({ mode: 'local', url: '' }),
    call: async (request) => {
      calls.push(request);
      if (request.cmd === 'settings_get') return { ok: true, settings: { experimental_message_queue: true } };
      if (request.cmd === 'llm_status') return { ok: true, status: { model: 'fixture', models: [] } };
      return { ok: true };
    },
  };
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const values = await ctrl.configItems();
  assert.equal(values.experimental_message_queue, true, 'queue toggle reads settings');
  assert.equal(values.experimental_evidence_reviews, false, 'reviews toggle defaults off');
  // 保存走 settings_save
  await ctrl.configSet('experimental_message_queue', false);
  const save = calls.find((c) => c.cmd === 'settings_save' && c.settings?.experimental_message_queue === false);
  assert.ok(save, 'toggle save hits settings_save');
}

// 3) 全量扁平索引含全部组（panelRows 预算依赖 flattenConfig）
const rows = flattenConfig();
assert.ok(rows.some((r) => r.group === '实验能力'), 'flatten includes experiment group');
assert.ok(rows.length >= 11, `config rows count ${rows.length} >= 11`);

console.log('Config toggles: experiment switches registered, read/save wired, flatten budgeted');
process.exit(0);
