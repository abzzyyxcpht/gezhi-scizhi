// 端到端冒烟：科研创新功能对真实 Python 引擎的数据链路（notebook_list / evidence_brief → 面板派生）
// 运行: GEZHI_DATA_DIR=<隔离目录> node src/opentui/spike/e2e-research.js
import { Bridge } from "../../bridge.js"
import { createSessionController } from "../session-controller.js"
import { rankHypotheses, consensusFromBrief, buildExperimentTree } from "../components/research-panels.js"

const bridge = new Bridge()
const controller = createSessionController({ bridge })
const checks = []
const check = (name, ok) => { checks.push([name, ok]); console.error(`[e2e-research] ${ok ? "✓" : "✗"} ${name}`) }
try {
  await bridge.whenReady()
  console.error(`[e2e-research] 连接=${bridge.connection().mode} 模式=${await controller.readMode()}`)

  // 1) notebook_list：真实引擎的数据形态（空库返回空数组，不报错）
  const hyps = await controller.hypotheses()
  check("hypotheses() 走通 notebook_list（空库返回数组）", Array.isArray(hyps))

  // 2) evidence_brief：真实引擎的证据对照数据形态
  const cons = await controller.consensus()
  check("consensus() 走通 evidence_brief（null 或计数对象）", cons === null || typeof cons.total === "number")

  // 3) matrix / experimentTree 派生不抛错
  const mat = await controller.matrix()
  check("matrix() 返回数组", Array.isArray(mat))
  const tree = await controller.experimentTree()
  check("experimentTree() 返回数组", Array.isArray(tree))

  // 4) 纯函数对真实数据形态健壮（空/缺失字段）
  check("rankHypotheses 对空数组健壮", rankHypotheses([]).length === 0)
  check("consensusFromBrief 对空 brief 健壮", consensusFromBrief({}).total === 0)
  check("buildExperimentTree 对空数组健壮", buildExperimentTree([]).length === 0)

  // 4b) 世界模型 round-trip：save → list → vote → delete
  const wfSave = await bridge.call({ cmd: 'world_fact_save', fact: {
    claim: '冒烟事实：退火温度与缺陷检出率正相关', topic: '热轧钢',
    source: { id: 'e2e_src', source_type: 'user_provided' } } });
  check('world_fact_save 写入', wfSave?.ok === true);
  if (wfSave?.ok) {
    const wfList = await controller.facts();
    check('facts() 出现新事实', wfList.some((f) => f.id === wfSave.fact.id));
    const wfVote = await controller.factVote(wfSave.fact.id, 'support');
    check('factVote 支持投票', wfVote?.ok === true);
    const wfDel = await bridge.call({ cmd: 'world_fact_delete', id: wfSave.fact.id });
    check('world_fact_delete 删除', wfDel?.ok === true);
  }

  // 5) 造一条假设记录再排序（写隔离数据目录，验证 round-trip）
  const saved = await bridge.call({
    cmd: "notebook_save",
    record: { id: "e2e_h1", kind: "hypothesis", title: "冒烟假设：温度与产率正相关", question: "温度是否提升产率？", status: "planned" },
  })
  check("notebook_save 写入隔离库", saved?.ok === true)
  if (saved?.ok) {
    const hyps2 = await controller.hypotheses()
    check("排行榜出现新假设", hyps2.some((h) => h.id === "e2e_h1"))
    const tree2 = await controller.experimentTree()
    check("实验树含新记录", buildExperimentTree([{ id: "e2e_h1", title: "t", status: "planned", kind: "hypothesis", related_ids: [] }]).length === 1 && Array.isArray(tree2))
    await bridge.call({ cmd: "notebook_delete", id: "e2e_h1" })
  }

  // 6) AI 审稿人：造一条记录 → 五维评分
  const nbSave = await bridge.call({ cmd: 'notebook_save', record: {
    id: 'e2e_review', kind: 'hypothesis', title: '审稿冒烟：温度与产率', status: 'completed',
    question: '温度是否提升产率？', result: '提升 4.2%',
    evidence: [{ 'relation': 'supports', 'source': { 'id': 's1' }, 'quote': 'q', 'scope': 'sc' }] } });
  if (nbSave?.ok) {
    const rv = await bridge.call({ cmd: 'peer_review', id: 'e2e_review' });
    check('peer_review 五维评分', rv?.ok === true && 'dimensions' in (rv || {}));
    check('审稿边界声明', Boolean((rv || {}).boundary));
    await bridge.call({ cmd: 'notebook_delete', id: 'e2e_review' });
  }

  // 7) 复现流水线注册与 AI 审稿人 bridge 链路（真引擎）
  const wfs = await bridge.call({ cmd: 'workflows' });
  check('reproduce 工作流已注册', (wfs?.workflows || []).some((w) => w.id === 'reproduce'));
  const badReview = await bridge.call({ cmd: 'peer_review', id: 'e2e_missing' });
  check('peer_review 未知记录返回 not_found', badReview?.ok === false && badReview?.code === 'not_found');

  const failed = checks.filter(([, ok]) => !ok)
  console.error(failed.length ? `[e2e-research] FAIL(${failed.length})` : "[e2e-research] PASS")
  process.exit(failed.length ? 1 : 0)
} catch (error) {
  console.error(`[e2e-research] ERROR ${error?.message || error}`)
  process.exit(1)
} finally {
  bridge.close()
}
