// 宽度工具单测：CJK 按 2 列计；裁切与补白都不得越界。
// 回归锚点：文献池表格曾因 clipWide 多出 1 列而出现「…2026」粘连。
import { padWide, clipWide } from '../src/commands.js';
import stringWidth from 'string-width';

const width = s => stringWidth(String(s ?? ''));

let passed = 0;
const ok = (cond, msg) => {
  if (!cond) {
    console.error(`✘ ${msg}`);
    process.exit(1);
  }
  passed += 1;
};

const LONG = '大模型智能体 · Human oversight of large language model agents';

// clipWide：不越界、超限带省略号、未超限不改写
ok(width(clipWide(LONG, 44)) <= 44, 'clipWide 中文长题名裁切后不超过 44 列');
ok(width(clipWide('a'.repeat(80), 20)) <= 20, 'clipWide 英文超长裁切后不超过 20 列');
ok(clipWide('a'.repeat(80), 20).endsWith('…'), 'clipWide 超限时以省略号收尾');
ok(clipWide('短题名', 44) === '短题名', 'clipWide 未超限时原样返回');
ok(clipWide('', 10) === '', 'clipWide 空串安全');
ok(clipWide('12345', 5) === '12345', '恰好占满列宽时不截断');
ok(clipWide('中文', 4) === '中文', 'CJK 恰好占满列宽时不截断');
ok(clipWide('e\u0301clair', 3) === 'e\u0301c…', '组合字素不可拆开');
ok(clipWide('👨‍👩‍👧‍👦研究', 4) === '👨‍👩‍👧‍👦…', 'ZWJ emoji 保持完整');
ok(clipWide('abc', 0) === '', '零列无溢出省略号');
ok(clipWide('abc', 1) === '…', '单列仅显示省略号');

// padWide：补到目标列宽；已超宽则不截断
ok(width(padWide('中文', 8)) === 8, 'padWide 中文补到 8 列');
ok(width(padWide('abc', 8)) === 8, 'padWide 英文补到 8 列');
ok(width(padWide('e\u0301', 8)) === 8, '组合字符按实际列宽补白');
ok(width(padWide('👨‍👩‍👧‍👦', 8)) === 8, 'emoji 按实际列宽补白');
ok(width(padWide('超出宽度了', 4)) === 10, 'padWide 已超宽时不再补空格');

// 组合（表格列）：每列恰好 N 列，下一列才不会贴上来
const cell = (s, n) => padWide(clipWide(s, n), n);
ok(width(cell(LONG, 44)) === 44, '题名列恰好 44 列（年份列不会粘连）');
ok(width(cell('短', 44)) === 44, '短题名同样恰好 44 列');

console.log(`TUI 宽度工具测试通过：${passed} 项`);
