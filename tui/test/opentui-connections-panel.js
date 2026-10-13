// 连接总览面板（/connections）：model_registry.describe 归一 + 窗口化渲染 +
// 光标只落条目行 + 溢出指示/滚动；小终端不溢出（预算与渲染同源）。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import {
  connectionsRowModel, connectionsGeometry, connectionsScrollFor, connectionsCursorRow,
} from '../src/opentui/components/connections-panel.js';
import { createSessionController } from '../src/opentui/session-controller.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

// ---- 1) 纯函数：行模型（分组 / 生效标记 / 空态 / 凭据遮蔽）----
const populated = {
  connections: [
    { id: 'lab', label: '实验室网关', protocol: 'responses', base_url: 'https://gw.example.edu/v1', has_api_key: true, active: true },
    { id: 'backup', label: '备用镜像', protocol: 'chat_completions', base_url: 'https://user:pass@backup.example.edu/v1', has_api_key: false, active: false },
  ],
  profiles: [
    { id: 'lab-profile', label: '实验模型', connection_id: 'lab', model: 'profile-model', reasoning_effort: 'high', active: true },
  ],
  resolved: [
    { stage: 'planning', profile_id: 'lab-profile', connection_id: 'lab', model: 'profile-model', protocol: 'responses' },
    { stage: 'review', profile_id: 'legacy', connection_id: 'legacy', model: 'legacy-model', protocol: 'chat_completions' },
  ],
};
const rows = connectionsRowModel(populated);
assert.equal(rows.filter(r => r.kind === 'header').map(r => r.label).join('|'), '服务连接（2）|模型配置（1）|各阶段生效（2）');
assert.equal(rows.filter(r => r.kind === 'item').length, 5, '2 连接 + 1 模型 + 2 生效阶段');
assert.ok(rows.find(r => r.key === 'conn:lab').label.includes('◉'), '生效连接标记 ◉');
assert.ok(rows.find(r => r.key === 'conn:backup').label.includes('○'), '未生效连接标记 ○');
assert.ok(!rows.find(r => r.key === 'conn:backup').value.includes('user:pass'), 'URL 凭据不落面板');
assert.ok(rows.find(r => r.key === 'stage:review').label.includes('审查 → legacy'), '阶段中文标签 + 生效 profile');
assert.ok(rows.find(r => r.key === 'stage:planning').value.includes('连接 lab'), '生效阶段带连接来源');

const emptyRows = connectionsRowModel({});
assert.equal(emptyRows.filter(r => r.kind === 'note').length, 3, '空态三组各一条提示');
assert.ok(emptyRows.every(r => r.kind !== 'item'), '空态无条目');

// ---- 2) 纯函数：几何 / 滚动 / 光标映射 ----
for (const height of [18, 24, 28, 34, 60]) {
  const geom = connectionsGeometry(height, populated);
  assert.ok(geom.maxVisibleRows >= 4, `h=${height} 最小窗口`);
  assert.equal(geom.visibleRows, Math.min(geom.totalRows, geom.maxVisibleRows), `h=${height} 窗口钳制`);
  assert.equal(geom.panelRows, geom.visibleRows + 4 + (geom.overflows ? 2 : 0), `h=${height} 预算 == 渲染最坏行数`);
}
assert.equal(connectionsGeometry(24, populated).overflows, true, '24 行溢出（窗口化）');
assert.equal(connectionsGeometry(60, populated).overflows, false, '充足终端全量可见');

const cursorRows = rows.map((r, i) => (r.kind === 'item' ? i : -1)).filter(i => i >= 0);
assert.deepEqual(cursorRows, [1, 2, 4, 6, 7], '条目行号（跳过组标题）');
assert.equal(connectionsCursorRow(rows, 0), 1, '序号 0 → 首条目行');
assert.equal(connectionsCursorRow(rows, 4), 7, '序号 4 → 末条目行');
assert.equal(connectionsCursorRow(rows, 99), 7, '序号越界钳到末条目行');
assert.equal(connectionsCursorRow(emptyRows, 0), 0, '空态回行 0');

{
  let scroll = 0;
  for (let ordinal = 0; ordinal < 5; ordinal++) {
    const row = connectionsCursorRow(rows, ordinal);
    scroll = connectionsScrollFor(rows, row, scroll, 4);
    assert.ok(row >= scroll && row < scroll + 4, `序号 ${ordinal} 光标可见`);
    assert.ok(scroll >= 0 && scroll <= Math.max(0, rows.length - 4), '滚动起点钳制');
  }
  assert.equal(connectionsScrollFor(rows, 7, 99, 4), 4, '滚动越界钳到最大（光标在窗口内）');
  assert.equal(connectionsScrollFor(rows, 0, 99, 4), 0, '光标回顶时滚动跟着回顶（光标恒可见）');
}

// ---- 3) 控制器：model_registry.describe 归一（含失败降级）----
{
  const calls = [];
  const bridge = {
    connection: () => ({ mode: 'local', url: '' }),
    call: async (request) => {
      calls.push(request);
      if (request.cmd === 'model_registry') {
        return { ok: true, registry: {
          connections: [{ id: 'lab', label: '实验室网关', has_api_key: true, base_url: 'https://gw.example.edu/v1' }],
          profiles: [{ id: 'p1', connection_id: 'lab', model: 'profile-model' }],
          routes: { planning: 'p1' },
          resolved: { planning: { profile_id: 'p1', connection_id: 'lab', model: 'profile-model', protocol: 'responses' } },
        } };
      }
      return { ok: true };
    },
  };
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const data = await ctrl.connections();
  assert.equal(calls[0].cmd, 'model_registry', '连接总览走 model_registry');
  assert.equal(data.connections.length, 1);
  assert.equal(data.connections[0].active, true, 'resolved 引用即生效');
  assert.equal(data.profiles[0].active, true);
  assert.equal(data.resolved[0].stage, 'planning');
  assert.equal(data.resolved[0].protocol, 'responses');
}

{
  const bridge = { connection: () => ({ mode: 'local', url: '' }), call: async () => ({ ok: false, error: 'boom' }) };
  const ctrl = createSessionController({ bridge, sleep: () => Promise.resolve() });
  const data = await ctrl.connections();
  assert.deepEqual(data, { connections: [], profiles: [], routes: {}, resolved: [] }, '失败降级为空结构（不抛）');
}

// ---- 4) 集成：80x28 打开 → 溢出指示 → ↓ 滚动 → Esc 关闭 ----
await upgradeFixture(async t => {
  t.resize(80, 28);
  await t.press('');
  await t.command('/connections');
  await t.wait(() => t.frame().includes('连接总览'), '/connections opens');
  assert.ok(t.frame().includes('实验室网关') && t.frame().includes('密钥已配置'), '连接条目与密钥状态');
  assert.ok(t.frame().includes('备用镜像') && t.frame().includes('密钥未配置'), '第二个连接');
  assert.ok(t.frame().includes('↓ 更多'), '首屏溢出指示');
  assert.ok(t.frame().includes('写操作走 /connection'), '页脚提示写操作入口');

  for (let i = 0; i < 8; i++) await t.press('\x1b[B');
  assert.ok(t.frame().includes('↑ 更多'), '滚动后顶部指示');
  assert.ok(t.frame().includes('审查 → legacy'), '末条目进入窗口');
  t.capture('connections-panel-28', 80, 28);

  await t.press('\x1b');
  await t.wait(() => !t.frame().includes('写操作走 /connection'), 'Esc 关闭');
  assert.ok(!t.frame().includes('写操作走 /connection'));
});

console.log('Connections panel: registry normalization, cursor/scroll invariants, windowed frames at 80x28');
process.exit(0);
