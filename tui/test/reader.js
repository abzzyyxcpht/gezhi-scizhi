// 全屏阅读器：窗口完整性不变量——
// gutter 行号序列连续不断档、首屏从第 1 行起、翻页/逐行/到底不越界、标题总行数一致。
// 注：OpenTUI ReaderPanel 表头为「「标题」 · N 行」+ 进度条（非 Ink 的「行 a–b / total」），
// 不变量按 OpenTUI 口径改写：可见行号必须是连续区间，长度等于面板可视行数。
import assert from 'node:assert/strict';
import path from 'node:path';
import { fileURLToPath } from 'node:url';
import { Bridge } from '../src/bridge.js';
import { upgradeFixture } from './upgrade-ui-fixture.js';

const LINES = 60;
const doc = Array.from({ length: LINES },
  (_, i) => `L${String(i + 1).padStart(2, '0')} 研究要点 ${i + 1}`).join('\n');

export const captureReaderFrames = () => upgradeFixture(async t => {
  const originalCall = Bridge.prototype.call;
  Bridge.prototype.call = async function (request) {
    t.calls.push(request);
    if (request.cmd === 'command' && String(request.text || '') === '/read outline') {
      return { ok: true, text: doc };
    }
    return originalCall.call(this, request);
  };
  try {
    /** 实际渲染出来的（gutter 行号, 内容行号）对（阅读器边框行）。 */
    const gutterRows = () => t.frame().split('\n')
      .map((l) => (l.match(/│\s*(\d+)\s+L(\d+)/) || []).slice(1).map(Number))
      .filter((pair) => pair.length === 2);
    /** 核心不变量：可见行号连续不断档，且每行 gutter 与内容行号一一对应。 */
    const assertWindowIntegrity = (label) => {
      const rows = gutterRows();
      assert.ok(rows.length >= 5, `${label}：gutter 行数异常（${rows.length}）\n${t.frame()}`);
      assert.ok(rows.every(([gutter, content]) => gutter === content),
        `${label}：行号与内容错位\n${t.frame()}`);
      const got = rows.map(([gutter]) => gutter);
      const want = Array.from({ length: got.length }, (_, i) => got[0] + i);
      assert.deepEqual(got, want, `${label}：行号断档（${got.join(',')}）`);
      return got;
    };
    const totalFromTitle = () => Number((t.frame().match(/综述框架」\s*·\s*(\d+)\s*行/) || [])[1]);

    await t.command('/read outline');
    await t.wait(() => t.frame().includes('综述框架'), 'open outline reader');
    const first = assertWindowIntegrity('首屏窗口');
    assert.equal(first[0], 1, 'first window starts at line 1');
    assert.equal(totalFromTitle(), LINES, 'title reports the full line count');
    assert.ok(t.frame().includes('L01 研究要点 1'), 'first line visible');
    // → 前进十行：窗口内容与行号同步移动
    await t.press('\x1b[C');
    await t.wait(() => gutterRows()[0]?.[0] === 11, '→ advances ten lines');
    const second = assertWindowIntegrity('翻页后窗口');
    assert.ok(t.frame().includes('L11 研究要点 11'), 'line 11 visible after page');
    assert.ok(!t.frame().includes('L01 研究要点 1'), 'old window replaced');
    // ↓ 前进一行
    await t.press('\x1b[B');
    await t.wait(() => gutterRows()[0]?.[0] === 12, '↓ advances one line');
    assertWindowIntegrity('逐行滚动后窗口');
    // 连按 PgDn 整页到底：offset 钳制在末窗，不越界，末行可见
    for (let i = 0; i < 8; i += 1) await t.press('\x1b[6~');
    await t.wait(() => t.frame().includes('L60 研究要点 60'), 'PgDn reaches the end');
    const last = assertWindowIntegrity('到底窗口');
    assert.equal(last[last.length - 1], LINES, 'bottom window ends at the last line');
    assert.ok(t.frame().includes('100%'), 'progress bar reaches 100%');
    // q 返回
    await t.press('q');
    await t.wait(() => !t.frame().includes('q/Esc 返回'), 'q returns to composer');
  } finally {
    Bridge.prototype.call = originalCall;
  }
});

if (path.resolve(process.argv[1] || '') === fileURLToPath(import.meta.url)) {
  const result = await captureReaderFrames();
  console.log(`TUI 阅读器测试通过（窗口完整性）：${result.frames.length} 帧`);
  process.exit(0);
}
