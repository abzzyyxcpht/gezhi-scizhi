"""Bounded, read-only comparison of recorded supporting and challenging evidence.

Relations and material scopes are declarations in the notebook. This view never
verifies scientific support, fetches a source, or schedules a follow-up itself.
"""
from __future__ import annotations

from collections import Counter
import re

from . import research_notebook
from .workbench import redact

_ID = re.compile(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}\Z')
_CONTROLS = re.compile(r'[\x00-\x08\x0b-\x1f\x7f]')
BOUNDARY = ('支持、反驳和材料范围均为录入者声明，未验证来源真实性或科学支持。'
            '合成材料仅供演示；缺少反驳材料不表示结论已经成立。')


def _display(value, maximum):
    return _CONTROLS.sub('�', str(redact(str(value or ''))))[:maximum]


def _record(record):
    evidence = record.get('evidence') or []
    counts = Counter(item.get('relation') for item in evidence)
    groups = {name: [] for name in ('supports', 'challenges', 'context')}
    identities = set()
    synthetic_count = 0
    for item in evidence:
        source = item.get('source') or {}
        identity = source.get('doi') or source.get('id') or source.get('url')
        if identity:
            identities.add(str(identity))
        synthetic = bool(record.get('synthetic') or source.get('synthetic')
                         or source.get('source_type') == 'synthetic_demo')
        synthetic_count += int(synthetic)
        relation = item.get('relation')
        if relation not in groups or len(groups[relation]) >= 20:
            continue
        original = str(item.get('quote') or '')
        quote = _display(original, 2000)
        groups[relation].append({
            'source': {**{key: _display(source.get(key), 500) for key in
                        ('id', 'title', 'doi', 'url', 'source_type')}, 'synthetic': synthetic},
            'quote': quote, 'scope': _display(item.get('scope'), 256),
            'locator': _display(item.get('locator'), 256), 'note': _display(item.get('note'), 600),
            'synthetic': synthetic, 'quote_truncated': len(original) > 2000,
            'quote_modified': quote != original,
        })
    gaps = []
    if not counts['supports']:
        gaps.append('缺少支持材料')
    if not counts['challenges']:
        gaps.append('缺少反驳材料')
    if not evidence:
        gaps.append('尚未录入证据')
    elif synthetic_count == len(evidence):
        gaps.append('当前仅有合成材料')
    title = _display(record.get('title'), 240)
    question = _display(record.get('question'), 1200)
    target = '优先检索反驳证据和不成立的条件' if not counts['challenges'] else '逐项核对支持与反驳材料的实验条件和适用范围'
    draft = (f'请复核研究记录《{title}》（记录 {record["id"]}，版本 {record["version"]}）。\n'
             f'研究问题：{question}\n'
             f'当前录入支持 {counts["supports"]} 条、反驳 {counts["challenges"]} 条，'
             f'其中合成材料 {synthetic_count} 条。\n'
             f'请{target}，注明来源、材料范围、局限和未验证之处；不要把原文命中当作科学支持已经成立。')
    return {'id': record['id'], 'version': record['version'], 'title': title, 'question': question,
            'kind': record.get('kind'), 'status': record.get('status'), 'groups': groups,
            'counts': {**{name: counts[name] for name in groups}, 'synthetic': synthetic_count,
                       'unique_sources': len(identities)},
            'gaps': gaps, 'followup_draft': draft,
            'provenance': {'scientific_verification': 'not_performed',
                           'evidence_relations': (record.get('provenance') or {}).get('evidence_relations', 'unknown')},
            'evidence_truncated': any(counts[name] > len(groups[name]) for name in groups)}


def handle(request):
    if not isinstance(request, dict) or set(request) - {'cmd', 'task_id'} or request.get('cmd') != 'evidence_brief':
        return {'ok': False, 'error': '证据对照仅接受当前任务范围。'}
    task_id = request.get('task_id')
    if task_id is not None and (not isinstance(task_id, str) or (task_id and not _ID.fullmatch(task_id))):
        return {'ok': False, 'error': '科研任务 ID 无效。'}
    task_id = task_id or None
    query = {'cmd': 'notebook_list'}
    if task_id is not None:
        query['task_id'] = task_id
    result = research_notebook.handle(query)
    if not result.get('ok'):
        return result
    records = result['records']
    return {'ok': True, 'brief': {'scope': {'task_id': task_id}, 'records': [_record(row) for row in records[:20]],
            'total_records': len(records), 'visible_records': min(20, len(records)),
            'records_truncated': len(records) > 20, 'boundary': BOUNDARY}}


def render_text(brief):
    lines = ['证据对照 · ' + (brief.get('scope', {}).get('task_id') or '全部研究记录'), brief['boundary'], '']
    labels = {'supports': '支持', 'challenges': '反驳', 'context': '背景'}
    for row in brief['records']:
        lines += [f'{row["title"]} · {row["id"]} · 版本 {row["version"]}', row['question']]
        for relation, label in labels.items():
            lines.append(f'{label}材料 {row["counts"][relation]} 条')
            for item in row['groups'][relation]:
                source = item['source']
                identity = source['doi'] or source['id'] or source['url']
                lines.append(f'  {identity} · 范围声明：{item["scope"]}' + (' · 合成样例' if item['synthetic'] else ''))
                lines.append('  ' + item['quote'])
                if item['quote_modified']:
                    lines.append('  [展示已截断、脱敏或清理控制字符；完整原记录另存。]')
        if row['gaps']:
            lines.append('待补：' + '；'.join(row['gaps']))
        if row['evidence_truncated']:
            lines.append('每类最多展示20条材料，计数包含未显示材料；完整记录请打开研究记录。')
        lines += ['下一问草稿（未发送）：', row['followup_draft'], '']
    if not brief['records']:
        lines.append('当前范围尚无研究记录，可先在研究记录中录入支持/反驳材料。')
    if brief['records_truncated']:
        lines.append(f'显示最近20条，共{brief["total_records"]}条；完整记录请使用研究记录入口。')
    return '\n'.join(lines)
