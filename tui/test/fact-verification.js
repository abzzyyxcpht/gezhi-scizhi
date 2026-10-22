// 事实核验语义（L02）：支持票不等于核验。
// 后端：票数只在 asserted/challenged 之间取舍，verified 必须来自独立核验记录
// （verified_by + verified_note + verified_at 缺一不可，且材料非合成）。
// 前端：verified 文案不得再宣称「已核验」，颜色不再用 success。
// 运行: node test/fact-verification.js
import assert from 'node:assert/strict';
import fs from 'node:fs';
import os from 'node:os';
import path from 'node:path';
import { spawnSync } from 'node:child_process';
import { fileURLToPath } from 'node:url';
import { FACT_STATUS_COLOR, FACT_STATUS_LABEL } from '../src/opentui/components/research-panels.js';
import { TOKENS } from '../src/opentui/theme/tokens.js';

const root = fileURLToPath(new URL('../..', import.meta.url));
const scratch = fs.mkdtempSync(path.join(os.tmpdir(), 'scizhi-fact-verification-'));
const data = path.join(scratch, 'data');
fs.mkdirSync(data, { recursive: true });

// 后端探针：直接调用世界模型 handle，隔离数据目录，不联网、不调模型。
const probe = path.join(scratch, 'probe.py');
fs.writeFileSync(probe, `import json, os, sys
sys.path.insert(0, os.getcwd())
from src import research_world

def call(req):
    return research_world.handle(req)

def row(fid):
    return call({"cmd": "world_fact_get", "id": fid})["fact"]

base = {"claim": "支持票不等于核验", "source": {"id": "u1", "source_type": "user_provided"}}
record = {"status": "verified", "verified_by": "reviewer-a",
          "verified_note": "recount of the raw run matches",
          "verified_at": "2026-10-20T09:30:00Z"}
out = {}

call({"cmd": "world_fact_save", "fact": dict(base, id="wf_vote")})
for _ in range(3):
    call({"cmd": "world_fact_support", "id": "wf_vote"})
item = row("wf_vote")
out["after_supports"] = [item["status"], item["supports"]]
for _ in range(4):
    call({"cmd": "world_fact_challenge", "id": "wf_vote"})
item = row("wf_vote")
out["after_challenges"] = [item["status"], item["challenges"]]

saved = call({"cmd": "world_fact_save", "fact": dict(base, id="wf_ok", **record)})
out["verified_saved"] = [saved["ok"], row("wf_ok")["status"]]
call({"cmd": "world_fact_challenge", "id": "wf_ok"})
out["verified_after_vote"] = row("wf_ok")["status"]

call({"cmd": "world_fact_save", "fact": dict(base, id="wf_partial",
                                            status="verified", verified_by="reviewer-a")})
item = row("wf_partial")
out["partial"] = [item["status"], item["notes"]]

call({"cmd": "world_fact_save", "fact": dict(base, id="wf_legacy")})
store = os.path.join(os.environ["GEZHI_DATA_DIR"], "world_facts.json")
with open(store, encoding="utf-8") as handle:
    payload = json.load(handle)
payload["facts"]["wf_legacy"]["status"] = "verified"
with open(store, "w", encoding="utf-8") as handle:
    json.dump(payload, handle, ensure_ascii=False)
item = row("wf_legacy")
out["legacy"] = [item["status"], item["notes"]]

call({"cmd": "world_fact_save", "fact": dict(base, id="wf_syn", synthetic=True, **record)})
item = row("wf_syn")
out["synthetic"] = [item["status"], item["notes"]]
print(json.dumps(out, ensure_ascii=False))
`, 'utf8');

const env = { ...process.env, GEZHI_DATA_DIR: data, PYTHONUTF8: '1',
  PYTHONIOENCODING: 'utf-8', PYTHONDONTWRITEBYTECODE: '1',
  GEZHI_REMOTE_URL: '', GEZHI_REMOTE_TOKEN: '',
  GEZHI_LLM_BASE_URL: '', GEZHI_LLM_API_KEY: '', GEZHI_LLM_MODEL: '' };
const run = spawnSync('python', ['-B', probe], { cwd: root, env, encoding: 'utf8' });
assert.ifError(run.error);
assert.equal(run.status, 0, `后端探针失败：${run.stderr || run.stdout}`);
const observed = JSON.parse(run.stdout.trim().split('\n').pop());

// ① 票数只决定 asserted/challenged，绝不产生 verified
assert.deepEqual(observed.after_supports, ['asserted', 3], '三张支持票仍应为 asserted');
assert.deepEqual(observed.after_challenges, ['challenged', 4], '反驳领先才迁移为 challenged');

// ② 只有独立核验记录（核验人 + 说明 + 时间）才 verified
assert.deepEqual(observed.verified_saved, [true, 'verified'], '三项齐全才 verified');
assert.equal(observed.verified_after_vote, 'verified', '核验记录不被后续票数改写');
assert.equal(observed.partial[0], 'asserted', '缺少核验说明/时间不能算核验');
assert.ok(observed.partial[1].includes('缺少独立核验记录'), '降级理由写入 notes');
assert.equal(observed.legacy[0], 'asserted', '历史 verified 无独立记录按 asserted 处理');
assert.ok(observed.legacy[1].includes('缺少独立核验记录'), '历史降级同样写 notes');
assert.equal(observed.synthetic[0], 'asserted', '合成材料不能标记 verified');
assert.ok(observed.synthetic[1].includes('合成材料'), '合成降级理由写入 notes');

// ③ 前端不再宣称「已核验」，颜色不再用 success
assert.ok(!FACT_STATUS_LABEL.verified.includes('已核验'), 'verified 文案不得再宣称已核验');
assert.ok(FACT_STATUS_LABEL.verified.includes('已复核'), 'verified 改为不越界的复核表述');
assert.equal(FACT_STATUS_LABEL.asserted, '已录入', '已录入保持原义');
assert.equal(FACT_STATUS_LABEL.challenged, '受反驳', '受反驳保持原义');
assert.equal(FACT_STATUS_LABEL.synthetic, '模拟', '模拟保持原义');
assert.notEqual(FACT_STATUS_COLOR.verified, TOKENS.success, 'verified 不再使用 success 色');
assert.equal(FACT_STATUS_COLOR.asserted, TOKENS.info, 'asserted 仍为 info');
assert.equal(FACT_STATUS_COLOR.challenged, TOKENS.error, 'challenged 仍为 error');
const panelSource = fs.readFileSync(
  new URL('../src/opentui/components/research-panels.js', import.meta.url), 'utf8');
assert.ok(!panelSource.includes('已核验'), '面板源码不再出现「已核验」');

fs.rmSync(scratch, { recursive: true, force: true });
console.log('fact-verification: PASS');
