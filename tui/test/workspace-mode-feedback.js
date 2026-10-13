// 模式切换反馈（Ink workspace-mode-feedback 的 OpenTUI 移植）：启动设置不是切换回执、
// 成功切换有明确确认、失败保留已提交模式徽标并可重试。Ink 版测 WorkspaceModeHeader 组件
// （过渡帧节拍/卸载计时器随组件删除）；OpenTUI 等价契约经真 runtime 断言，reducedMotion 直切。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

export const captureModeFeedbackFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  let failNext = false;
  Bridge.prototype.call = async function (request) {
    if (request.cmd === 'workbench_mode') {
      if (failNext) return { ok: false, error: 'fixture：模式保存失败' };
      return { ok: true, mode: request.mode || 'general' };
    }
    return originalCall.call(this, request);
  };
  try {
    await t.wait(() => t.frame().includes('· 输入'), 'fixture ready');
    assert.ok(!t.frame().includes('已切换'), 'startup settings are not a switch receipt');
    await t.command('/mode research');
    await t.wait(() => t.frame().includes('已切换到科研模式') && t.frame().includes('◎'), 'successful switch is acknowledged');
    failNext = true;
    await t.command('/mode general');
    await t.wait(() => t.frame().includes('fixture：模式保存失败'), 'failed switch reports the backend error');
    assert.ok(t.frame().includes('◎'), 'failed switch keeps the committed mode badge');
    failNext = false;
    await t.command('/mode general');
    await t.wait(() => t.frame().includes('已切换到通用模式') && t.frame().includes('◇'), 'retry after failure succeeds');
    t.capture('mode-feedback', 80, 24);
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureModeFeedbackFrames();
  console.log(`${result.frames.length} mode feedback real OpenTUI frames: no fake startup receipt, acknowledged switch and failure rollback passed`);
}
// opentui 渲染器保活计时器：直接运行时必须显式退出（被 capture 脚本 import 时不退出）
if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) process.exit(0);
