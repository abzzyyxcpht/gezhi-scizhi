// 终态标记映射：只有成功状态显示对勾，受阻/取消/中断/失败不得冒充完成。
import assert from 'node:assert/strict';
import { renderEvents } from '../src/opentui/session-controller.js';

const terminal = status => renderEvents([{ kind: 'completed', payload: { status } }])[0]?.text ?? '';

assert.equal(terminal('done'), '✓ 会话完成（done）', 'done 显示成功标记');
assert.equal(terminal('completed'), '✓ 会话完成（completed）', 'completed 显示成功标记');
assert.equal(terminal('blocked'), '✗ 已阻止（blocked）', 'blocked 显示失败标记');
assert.equal(terminal('cancelled'), '⏹ 已取消（cancelled）', 'cancelled 显示取消标记');
assert.equal(terminal('cancelling'), '⏹ 已取消（cancelling）', 'cancelling 显示取消标记');
assert.equal(terminal('error'), '✗ 出错（error）', 'error 显示失败标记');
assert.equal(terminal('failed'), '✗ 失败（failed）', 'failed 显示失败标记');
assert.equal(terminal('partial'), '◐ 部分完成（partial）', 'partial 显示部分完成标记');
assert.equal(terminal('interrupted'), '⏹ 已中断（interrupted）', 'interrupted 显示中断标记');
assert.equal(terminal('paused'), '○ paused', '未映射状态用中性标记并保留原始状态');
assert.equal(terminal(undefined), '✓ 会话完成（done）', '缺省状态按 done 处理');

for (const status of ['blocked', 'cancelled', 'cancelling', 'error', 'failed', 'partial', 'interrupted', 'paused']) {
  assert.ok(!terminal(status).includes('✓'), `${status} 终态不得带成功对勾`);
}

const errorLine = renderEvents([{ kind: 'error', payload: { message: 'boom' } }])[0]?.text ?? '';
assert.equal(errorLine, '✗ 出错：boom', 'error 事件分支保持原样');

console.log('Terminal state marks: success-only checkmark, blocked/cancelled/error/failed/partial/interrupted and unknown status passed');
