// HITL 菜单：
// 纯函数层（src/commands.js 活模块）——matchMenu 数字命中/越界/空格容忍原样保留。
// 注：Ink 的「待人工确认内联菜单」渲染在 OpenTUI 由审批面板（数字键决策）承接，
// 端到端关口→菜单→数字选择由 opentui-runtime-ui 审批面板测试覆盖；
// 任务级关口菜单（/pool 复核等动作）记录为 parity 缺口（见 docs/opentui-parity.md）。
import assert from 'node:assert/strict';
import { REVIEW_MENU, matchMenu } from '../src/commands.js';

if (matchMenu('3', REVIEW_MENU)?.cmd !== '/pool 10') throw new Error('matchMenu 未匹配 3');
if (matchMenu('9', REVIEW_MENU) !== null) throw new Error('matchMenu 不应匹配 9');
if (matchMenu(' 2 ', REVIEW_MENU)?.cmd !== '/drop-last') throw new Error('matchMenu 未处理空格');
if (matchMenu('研究', REVIEW_MENU) !== null) throw new Error('非数字不应匹配');
console.log('菜单纯函数校验通过（matchMenu：数字命中 / 非数字与越界不命中 / 空格容忍）');
process.exit(0);
