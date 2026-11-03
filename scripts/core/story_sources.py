"""Direct story-text references for new packages; never infer paragraphs from legacy event summaries."""
from __future__ import annotations

import argparse
import hashlib
import json
import re
import sys
from pathlib import Path

from .schema_contracts import require_schema
from .artifacts import project_path
from .director_reading import director_scene_reading, readable_fields


def _dialogue_comparison_text(text):
    """Only whitespace/punctuation; never normalize numerals or meaning."""
    import unicodedata
    compact = ''.join(c for c in text if not c.isspace())
    def significant(index, char):
        # Numeric separators/signs are content, not sentence punctuation.
        before = index > 0 and compact[index - 1].isdecimal()
        after = index + 1 < len(compact) and compact[index + 1].isdecimal()
        return (before and after or char in '%‰‱％'
                or after and char in '-‐‑‒–—﹣－')
    return ''.join(c for i, c in enumerate(compact)
                   if not unicodedata.category(c).startswith('P') or significant(i, c))


def _origin(row):
    """Origin is provenance, never an approval or audio verification."""
    value = row.get('origin')
    if 'origin' not in row:
        return 'source_reproduction'
    if not isinstance(value, dict) or value.get('kind') not in {
            'source_reproduction', 'authored_addition', 'authorized_revision'}:
        raise ValueError('对白 origin 类型无效')
    kind = value['kind']
    if kind != 'source_reproduction':
        for key in ('reference', 'scope', 'author', 'purpose', 'revision'):
            if not isinstance(value.get(key), str) or not value[key].strip():
                raise ValueError('创作来源须有真实请求/授权引用、范围、作者、用途和修订')
    return kind


def _validate_source_projection(record, root):
    """Optional fidelity binding to an existing extraction, not another script."""
    import hashlib
    binding = record.get('source_baseline')
    changes = record.get('source_changes', [])
    if not isinstance(changes, list):
        raise ValueError('source_changes 须为授权变更数组')
    if binding is None:
        if changes:
            raise ValueError('source_changes 需要原始抽取基线')
        return False
    if (not isinstance(binding, dict) or set(binding) != {'path', 'sha256'}
            or any(not isinstance(binding[k], str) or not binding[k].strip() for k in binding)):
        raise ValueError('原片基线须绑定已有审核记录 path/sha256')
    path = project_path(root, binding['path'])
    if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != binding['sha256']:
        raise ValueError('原片基线文件缺失或字节改变')
    baseline = json.loads(path.read_text(encoding='utf-8-sig'))
    if not isinstance(baseline, dict) or baseline.get('source_baseline') is not None:
        raise ValueError('原片基线须直接引用原始抽取，不建立基线链')
    validate_dialogue_review(baseline, root)
    if any(_origin(row) != 'source_reproduction' for row in baseline['dialogues']):
        raise ValueError('原片基线不能包含作者新增或授权改编')
    originals = {row['dialogue_id']: row for row in baseline['dialogues']}
    sources = {source['source_id']: source for source in record['sources']}
    if any(sources.get(source['source_id']) != source for source in baseline['sources']):
        raise ValueError('原片基线的原始证据必须完整保留')
    deleted, reordered = set(), None
    for change in changes:
        if (not isinstance(change, dict) or any(not isinstance(change.get(k), str)
                or not change[k].strip() for k in ('reference', 'scope', 'reason'))):
            raise ValueError('原片变更须有真实授权引用、范围和理由')
        if change.get('action') == 'delete':
            ident = change.get('dialogue_id')
            if not isinstance(ident, str) or ident not in originals or ident in deleted:
                raise ValueError('删除授权须准确绑定唯一原片 DLG')
            deleted.add(ident)
        elif change.get('action') == 'reorder' and reordered is None:
            reordered = change.get('dialogue_ids')
            if not isinstance(reordered, list) or any(not isinstance(x, str) for x in reordered):
                raise ValueError('换序授权须列完整原片 DLG 顺序')
        else:
            raise ValueError('不支持或重复的原片变更；改文复用 editorial_proposal')
    projection = []
    for row in record['dialogues']:
        ident, kind = row['dialogue_id'], _origin(row)
        if kind == 'authored_addition':
            if ident in originals:
                raise ValueError('作者新增不能覆盖原片 DLG')
            continue
        original = originals.get(ident)
        if original is None or row['source_ids'] != original['source_ids']:
            raise ValueError('原片行须映射原始 DLG 及来源')
        if kind == 'source_reproduction' and row['text'] != original['text']:
            raise ValueError('原片复刻不得改字')
        if kind == 'authorized_revision' and row['editorial_proposal']['original_text'] != original['text']:
            raise ValueError('授权修订须准确映射基线原句')
        projection.append(ident)
    expected = [ident for ident in originals if ident not in deleted]
    if reordered is not None:
        if len(reordered) != len(expected) or set(reordered) != set(expected):
            raise ValueError('换序授权不能遗漏、重复或添加原片 DLG')
        expected = reordered
    if projection != expected:
        raise ValueError('原片投影存在未授权删除、换序或重复')
    return True


def validate_dialogue_review(record, project_root):
    """Check declared evidence and provenance, not pixels, sound or human approval."""
    import hashlib
    import math
    if not isinstance(record, dict) or not isinstance(record.get('sources'), list):
        raise ValueError('对白审核记录须为对象且 sources 为数组')
    sources = {}
    for source in record['sources']:
        if not isinstance(source, dict):
            raise ValueError('原始证据须为对象')
        ident = source.get('source_id')
        if not isinstance(ident, str) or not ident or ident in sources:
            raise ValueError('对白证据 ID 缺失或重复')
        if source.get('kind') not in {'subtitle_verbatim', 'asr_hypothesis'}:
            raise ValueError('原始证据类型只能为字幕逐字或 ASR 候选；解释修订另列')
        if any(not isinstance(source.get(k), str) or not source[k].strip()
               for k in ('text', 'evidence_path', 'evidence_sha256', 'locator', 'producer')):
            raise ValueError('原始证据缺少文字、路径、hash、定位或记录者/模型')
        path = project_path(project_root, source['evidence_path'])
        if not path.is_file() or hashlib.sha256(path.read_bytes()).hexdigest() != source['evidence_sha256']:
            raise ValueError('原始证据文件缺失或 hash 不符：' + ident)
        # ASR must point into the retained machine output, not a manually copied paraphrase.
        pointer = source.get('text_pointer')
        if source['kind'] == 'asr_hypothesis' and not pointer:
            raise ValueError('ASR 原始证据须定位原输出 text_pointer')
        if pointer is not None:
            if not isinstance(pointer, str) or not pointer.startswith('/'):
                raise ValueError('原始证据 text_pointer 无效')
            try:
                value = json.loads(path.read_text(encoding='utf-8-sig'))
                for part in pointer[1:].split('/'):
                    key = part.replace('~1', '/').replace('~0', '~')
                    value = value[int(key)] if isinstance(value, list) else value[key]
            except (ValueError, KeyError, IndexError, TypeError) as exc:
                raise ValueError('原始证据字段不可解析：' + ident) from exc
            if value != source['text']:
                raise ValueError('原始证据文字被改写：' + ident)
        sources[ident] = source
    rows = record.get('dialogues')
    if not isinstance(rows, list) or not rows:
        raise ValueError('对白审核记录需要非空 dialogues')
    conflicts, unresolved, verified_times, ids = [], [], [], set()
    for row in rows:
        if not isinstance(row, dict):
            raise ValueError('对白审核行须为对象')
        ident = row.get('dialogue_id')
        if not isinstance(ident, str) or not re.fullmatch(r'DLG-[A-Z0-9-]+', ident) or ident in ids:
            raise ValueError('对白 ID 无效或重复')
        ids.add(ident)
        kind = _origin(row)
        refs = row.get('source_ids')
        if (not isinstance(refs, list) or (not refs and kind != 'authored_addition') or any(not isinstance(x, str) or x not in sources for x in refs)
                or len(set(refs)) != len(refs) or not isinstance(row.get('text'), str) or not row['text']):
            raise ValueError('对白缺少精确原始证据引用或文字：' + ident)
        if kind == 'authored_addition' and (refs or row.get('editorial_proposal') is not None
                or row.get('source_locator') is not None or row.get('verified_timing') is not None):
            raise ValueError('作者新增不得伪造原片来源、原文修订或时码：' + ident)
        if row.get('origin') is not None and kind == 'source_reproduction' and row.get('editorial_proposal') is not None:
            raise ValueError('明确原片复刻不能混入修订；使用 authorized_revision')
        originals = [sources[x]['text'] for x in refs]
        # An assembly is one utterance spanning caption pages, not alternative ASR hypotheses.
        assembly = row.get('source_assembly')
        if assembly is not None:
            if (kind != 'source_reproduction' or not isinstance(assembly, dict)
                    or not isinstance(assembly.get('source_ids'), list) or not assembly['source_ids']
                    or any(not isinstance(x, str) or x not in refs for x in assembly['source_ids'])
                    or len(set(assembly['source_ids'])) != len(assembly['source_ids'])
                    or any(not isinstance(assembly.get(k), str) or not assembly[k].strip()
                           for k in ('reason', 'reference', 'reviewer'))):
                raise ValueError('字幕整理须绑定有序来源、同轮核对依据、理由与核对者：' + ident)
            if any(sources[x]['kind'] != 'subtitle_verbatim' for x in assembly['source_ids']):
                raise ValueError('字幕整理只能拼接逐字字幕，不能拼接 ASR 替代假设：' + ident)
            assembled = ''.join(sources[x]['text'] for x in assembly['source_ids'])
            if _dialogue_comparison_text(assembled) != _dialogue_comparison_text(row['text']):
                raise ValueError('字幕整理只能恢复标点空白，不能改词、删词或换序：' + ident)
            originals = [assembled] + [sources[x]['text'] for x in refs if x not in assembly['source_ids']]
        if len({_dialogue_comparison_text(t) for t in originals}) > 1:
            conflicts.append(ident)
        proposal = row.get('editorial_proposal')
        if kind != 'authored_addition' and (not any(_dialogue_comparison_text(row['text']) == _dialogue_comparison_text(t) for t in originals) or proposal is not None or kind == 'authorized_revision'):
            if (not isinstance(proposal, dict) or proposal.get('proposed_text') != row['text']
                    or proposal.get('original_text') not in originals
                    or not isinstance(proposal.get('reason'), str) or not proposal['reason'].strip()
                    or not isinstance(proposal.get('source_ids'), list) or not proposal['source_ids']
                    or not set(proposal['source_ids']).issubset(refs)
                    or proposal['original_text'] not in [sources[x]['text'] for x in proposal['source_ids']]):
                raise ValueError('解释修订须绑定原文、拟改原句、理由与本行证据：' + ident)
        resolution = row.get('verification')
        if kind != 'source_reproduction' and (row.get('verified_timing') is not None
                or (isinstance(resolution, dict) and resolution.get('method') != 'user_confirmation')):
            raise ValueError('创作/改编只能独立逐字用户确认，不能冒充听核或原片已核时码：' + ident)
        if resolution is not None:
            if (not isinstance(resolution, dict) or resolution.get('scope') != 'dialogue'
                    or resolution.get('dialogue_id') != ident or resolution.get('text') != row['text']
                    or resolution.get('method') not in {'human_listening', 'user_confirmation'}
                    or any(not isinstance(resolution.get(k), str) or not resolution[k].strip() for k in ('reference', 'reviewer'))):
                raise ValueError('对白核对记录须绑定本行原文、范围、真实依据与核对者：' + ident)
        speaker = row.get('speaker_id')
        speaker_status = row.get('speaker_status', 'unresolved')
        if speaker_status not in {'verified', 'inferred_unverified', 'unresolved'}:
            raise ValueError('说话人状态无效：' + ident)
        if isinstance(resolution, dict) and resolution.get('speaker_id') is not None and resolution['speaker_id'] != speaker:
            raise ValueError('对白核对记录的说话人与当前行不一致：' + ident)
        # Explicit unknown sentinels are not an identity, even if marked verified.
        unknown_speaker = (isinstance(speaker, str) and speaker.strip().casefold() in
                           {'unknown', 'unknown_speaker', 'unk', 'unresolved', '未知', '待确认', '未确认'})
        # Legacy text-only verification is retained but cannot clear identity uncertainty.
        if (resolution is None or not isinstance(speaker, str) or not speaker.strip()
                or unknown_speaker or speaker_status != 'verified' or resolution.get('speaker_id') != speaker):
            unresolved.append(ident)
        for field in ('source_locator', 'verified_timing'):
            timing = row.get(field)
            if timing is None:
                continue
            if (not isinstance(timing, dict) or any(isinstance(timing.get(k), bool)
                    or not isinstance(timing.get(k), (int, float)) or not math.isfinite(timing[k]) for k in ('start','end'))
                    or timing['start'] < 0 or timing['end'] <= timing['start']):
                raise ValueError('时码须为有限、非负且递增的起止秒数：' + ident)
            if field == 'verified_timing':
                if not resolution or any(not isinstance(timing.get(k), str) or not timing[k].strip() for k in ('verification_ref', 'reviewer')):
                    raise ValueError('已核时码需要本行核对及独立对齐依据：' + ident)
                if ident in unresolved:
                    # Historical text-only receipts remain readable as declarations,
                    # never verified timing or production authority. New identity
                    # claims must satisfy the current contract rather than downgrade.
                    if 'speaker_status' not in row and 'speaker_id' not in resolution:
                        continue
                    raise ValueError('已核时码需要本行核对及独立对齐依据：' + ident)
                verified_times.append(ident)
    fidelity_checked = _validate_source_projection(record, project_root)
    return {'verification': 'source_consistency_only', 'conflicts': conflicts,
            'source_fidelity_checked': fidelity_checked,
            'unresolved_ids': unresolved, 'verified_timing_ids': verified_times,
            'limits': 'Hashes and declared provenance do not prove subtitle transcription, listening, identity, product claims or approval authenticity. Missing source_baseline cannot prove source completeness/order.'}


def read_dialogue_table(path: Path) -> list[tuple[str, str, str, str]]:
    """Read content columns only; later approval records may supersede row status."""
    text = path.read_text(encoding='utf-8-sig')
    section = re.search(r'^## 最终对白[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not section:
        return []
    rows = []
    escaped_text = False
    # A legacy dialogue may literally contain the marker. Only the actual
    # six-column header, before any DLG row, declares transport encoding.
    for line in section[1].splitlines():
        if re.match(r'^\s*\|\s*DLG-', line):
            break
        if not line.strip().startswith('|'):
            continue
        header = [cell.strip() for cell in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
        if len(header) == 6 and re.sub(r'\s+', '', header[0]) == '对白ID':
            escaped_text = header[2] == '原文（换行转义）'
            break
    for line in section[1].splitlines():
        if not re.match(r'^\s*\|\s*DLG-', line):
            continue
        cells = re.split(r'(?<!\\)\|', line.strip().strip('|'))
        if len(cells) != 6:
            raise ValueError('最终对白表必须保留六列：' + line)
        cells = [cell.strip().replace(r'\|', '|') for cell in cells]
        # Table-only transport escapes: decode once so escaped literal markup
        # cannot accidentally become a second newline. JSON/body keep raw text.
        if escaped_text:
            cells[2] = re.sub(r'<br\s*/?>|&lt;br(?:\s*/)?&gt;|&#38;',
                              lambda m: ('\n' if m[0].startswith('<') else
                                         '&' if m[0] == '&#38;' else
                                         m[0].replace('&lt;', '<').replace('&gt;', '>')),
                              cells[2])
        rows.append(tuple(cells[:4]))
    return rows


def validate_body_dialogues(narrative: dict) -> None:
    """Compare explicit speech declarations, never infer speakers from prose."""
    characters = {}
    for asset in narrative['asset_requirements']:
        if asset['category'] == 'CHARACTER':
            entity = asset['entity_id']
            # Existing packages use either CHR-LINLIN or LINLIN as speaker_id.
            for key in {entity, entity.removeprefix('CHR-')}:
                characters.setdefault(key, set()).add(asset['name'])
    declarations = []
    heading = re.compile(r'^\s*([^\n（）()]+?)[（(](DLG-[A-Z0-9-]+)[）)]\s*[：:]\s*', re.M)
    for paragraph in narrative['story_paragraphs']:
        text = paragraph['text']
        consumed = []
        matches = list(heading.finditer(text))
        # A DLG marker cannot silently escape parsing because its layout changed.
        if len(re.findall(r'\bDLG-[A-Z0-9-]+\b', text)) != len(matches):
            raise ValueError('正文对白标注无法完整识别：' + paragraph['paragraph_id'])
        for index, match in enumerate(matches):
            end = matches[index + 1].start() if index + 1 < len(matches) else len(text)
            remainder = text[match.end():end]
            quote_pairs = {'"': '"', '“': '”', '「': '」', '『': '』'}
            if remainder[:1] in quote_pairs:
                closing = quote_pairs[remainder[0]]
                speech = re.match(r'.(.*?)' + re.escape(closing) + r'(?=[ \t]*(?:\n|$))', remainder, re.S)
                if not speech:
                    raise ValueError('正文对白引号未闭合：' + match[2])
                spoken = speech[1]
                consumed.append((match.start(), match.end() + speech.end()))
            else:
                # Unquoted speech is one line; actions belong in separate prose.
                spoken = remainder.split('\n', 1)[0].strip()
                consumed.append((match.start(), match.end() + len(remainder.split('\n', 1)[0])))
            declarations.append((match[2], match[1].strip(), spoken, paragraph['paragraph_id']))
        # Inspect only text outside parsed speech: quoted dialogue may itself
        # contain colons, names and line breaks.
        remaining = list(text)
        for start, end in consumed:
            remaining[start:end] = ' ' * (end - start)
        names = set(characters) | {name for values in characters.values() for name in values}
        names.update(d['speaker_id'] for d in narrative['dialogues'])
        for line in ''.join(remaining).splitlines():
            match = re.match(r'^\s*([^：:\n]+)[：:]\s*(.*)$', line)
            if match and match[1].strip() in names:
                raise ValueError('人物发言缺少 DLG 标注：' + paragraph['paragraph_id'] + '：' + line.strip())
    expected = narrative['dialogues']
    if [row[0] for row in declarations] != [d['dialogue_id'] for d in expected]:
        raise ValueError('正文与对白表的编号或顺序不一致（遗漏、重复或未登记对白）')
    for (key, speaker, spoken, paragraph), dialogue in zip(declarations, expected):
        speaker_id = dialogue['speaker_id']
        names = characters.get(speaker_id, set())
        if speaker != speaker_id and (len(names) != 1 or speaker not in names):
            raise ValueError('正文说话者与对白表不一致或人物映射不明：' + key)
        if spoken != dialogue['text'] or paragraph != dialogue['source_paragraph_id']:
            raise ValueError('正文对白文字或来源段落与对白表不一致：' + key)


def read_story_paragraphs(path: Path) -> list[dict[str, str]]:
    text = path.read_text(encoding='utf-8-sig')
    body = re.search(r'^## 故事正文[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    if not body:
        raise ValueError('编导 Markdown 缺少故事正文')
    matches = list(re.finditer(r'^### (STP-[A-Z0-9-]+)(?:[ ｜|].*)?\n', body[1], re.M))
    if not matches or body[1][:matches[0].start()].strip():
        raise ValueError('故事正文必须从 STP 段落标题开始，不能遗漏未编号正文')
    result = []
    for index, match in enumerate(matches):
        end = matches[index + 1].start() if index + 1 < len(matches) else len(body[1])
        content = body[1][match.end():end].strip()
        if not content:
            raise ValueError('故事段落为空：' + match[1])
        result.append({'paragraph_id': match[1], 'text': content})
    if len({p['paragraph_id'] for p in result}) != len(result):
        raise ValueError('故事段落编号重复')
    return result


def validate_scene_ids(storyboard: dict) -> None:
    """Scene identities also become web anchors; creative segments do not duplicate them."""
    positions = {}
    for index, scene in enumerate(storyboard['scenes'], 1):
        key = scene['scene_id']
        if key in positions:
            raise ValueError(f'场景编号重复：{key}（位置 {positions[key]} 与 {index}）；同场创作段请合并到同一场景对象，不自动改号')
        positions[key] = index


def _candidate_text_source(narrative, root, evidence_id, paragraph_ids, storyboard):
    """Consume byte-bound historical text evidence, never import a confirmation."""
    from .candidate_test import resolve_inputs
    from .artifacts import digest
    selected = {r['artifact_id']: r for r in resolve_inputs(root, [evidence_id])}
    entry = selected[evidence_id]
    if entry.get('type') != 'source.evidence' or not entry.get('checksum'):
        raise ValueError('有限正文来源须为准确 hash 登记的 source.evidence')
    evidence = json.loads(project_path(root, entry['path_or_url']).read_text(encoding='utf-8-sig'))
    if evidence.get('purpose') != 'approved_text_candidate_input':
        raise ValueError('来源证据用途不是有限正文候选输入')
    source, approval = evidence.get('source'), evidence.get('approval')
    if not isinstance(source, dict) or not isinstance(approval, dict):
        raise ValueError('有限正文来源缺少源身份或历史批准依据')
    ids = [source.get('artifact_id'), approval.get('evidence_artifact_id')]
    if any(not isinstance(i, str) or not i for i in ids):
        raise ValueError('有限正文来源须指定准确已登记输入 ID')
    records = {r['artifact_id']: r for r in resolve_inputs(root, ids)}
    src, approval_entry = records[ids[0]], records[ids[1]]
    path = project_path(root, src['path_or_url'])
    if (not src.get('checksum') or src.get('logical_id') != 'narrative.package'
            or source.get('revision') != narrative.get('revision')
            or source.get('project_id') != narrative.get('project_id')
            or source.get('sha256') != digest(path)
            or json.loads(path.read_text(encoding='utf-8-sig')) != narrative):
        raise ValueError('有限正文来源项目、修订或字节与本轮叙事不一致')
    approval_path = project_path(root, approval_entry['path_or_url'])
    if (not approval_entry.get('checksum') or approval_entry.get('type') != 'source.evidence'
            or not isinstance(approval.get('reference'), str) or not approval['reference'].strip()
            or approval.get('sha256') != digest(approval_path)):
        raise ValueError('历史批准依据缺失、未登记或字节不符')
    allowed = evidence.get('paragraph_ids')
    known = {p['paragraph_id'] for p in narrative['story_paragraphs']}
    if (not isinstance(allowed, list) or not allowed
            or any(not isinstance(i, str) for i in allowed)
            or len(set(allowed)) != len(allowed) or not set(allowed) <= known):
        raise ValueError('有限正文来源须明确本轮准确段落范围')
    if storyboard is not None and (not paragraph_ids or not set(paragraph_ids) <= set(allowed)):
        raise ValueError('导演候选范围越出本轮有限正文来源')
    if storyboard is not None and not set(storyboard.get('source_paragraph_ids', [])) <= set(allowed):
        raise ValueError('导演包声明来源越出本轮有限正文来源')
    return {'candidate_source_evidence_id': evidence_id,
            'source_artifact_id': src['artifact_id'], 'source_sha256': digest(path),
            'allowed_paragraph_ids': allowed, 'historical_approval_reference': approval['reference'],
            'authority_scope': 'candidate_text_only', 'historical_evidence_semantics_checked': False,
            'source_fidelity_checked': False, 'human_listening_verified': False,
            'formal_adoption_allowed': False, 'asset_approval_inferred': False}


def validate_story_sources(narrative: dict, storyboard: dict | None = None, *, project_root: Path | None = None, candidate_test: bool = False,
                           candidate_paragraph_ids: list[str] | None = None,
                           candidate_source_evidence: str | None = None) -> dict | None:
    # Explicit read-only route, never inferred from missing coverage or open questions.
    local_candidate = candidate_paragraph_ids is not None
    if local_candidate and (storyboard is None or storyboard.get('status') != 'candidate'):
        raise ValueError('局部来源检查仅接受明确范围的 candidate 导演包')
    test_report = None
    if candidate_test:
        from .candidate_test import context, result_metadata
        if project_root is None or not context(project_root):
            raise ValueError("候选测试需要显式隔离项目状态")
        if narrative.get("status") != "candidate" or (storyboard is not None and storyboard.get("status") != "candidate"):
            raise ValueError("候选测试只消费 candidate 业务稿")
        test_report = {**result_metadata(), "unresolved_dialogue_ids": [], "source_fidelity_checked": False}
    require_schema(narrative, 'narrative-package.schema.json', label='编导包')
    if narrative.get('schema_version') != '2.0':
        raise ValueError('新段落引用要求编导包 2.0；旧事件包须按原稿重新整理，不能自动换编号')
    if candidate_source_evidence is not None:
        if test_report is None:
            raise ValueError('有限正文来源仅用于显式隔离 candidate_test，不是正式采用入口')
        test_report.update(_candidate_text_source(narrative, project_root, candidate_source_evidence,
                                                 candidate_paragraph_ids, storyboard))
        historical_path = narrative.get('dialogue_evidence_path')
        if historical_path:
            historical_file = project_path(project_root, historical_path)
            from .artifacts import digest
            if historical_file.is_file() and digest(historical_file) != narrative.get('dialogue_evidence_sha256'):
                raise ValueError('历史对白证据已存在但偏离绑定字节；不得以正文授权掩盖篡改')
            if historical_file.is_file():
                historical = json.loads(historical_file.read_text(encoding='utf-8-sig'))
                validate_dialogue_review(historical, project_root)
                if [(r['dialogue_id'], r.get('speaker_id'), r['text']) for r in historical['dialogues']] != [(r['dialogue_id'], r['speaker_id'], r['text']) for r in narrative['dialogues']]:
                    raise ValueError('历史对白证据存在但与正文不符；不得以正文授权掩盖冲突')
            test_report['dialogue_evidence_status'] = 'present_not_verified' if historical_file.is_file() else 'missing_unverified'
            test_report['dialogue_evidence_path'] = historical_path
        else:
            test_report['dialogue_evidence_status'] = 'not_bound_unverified'
        test_report['unresolved_dialogue_ids'] = [r['dialogue_id'] for r in narrative['dialogues']]
    if narrative.get('dialogue_evidence_path') and candidate_source_evidence is None:
        if project_root is None:
            raise ValueError('绑定对白审核记录需要 project_root')
        from .artifacts import digest
        evidence_path = project_path(project_root, narrative['dialogue_evidence_path'])
        if digest(evidence_path) != narrative.get('dialogue_evidence_sha256'):
            raise ValueError('对白审核记录已偏离绑定字节')
        evidence = json.loads(evidence_path.read_text(encoding='utf-8-sig'))
        report = validate_dialogue_review(evidence, project_root)
        if test_report is not None:
            test_report['source_fidelity_checked'] = report['source_fidelity_checked']
        if report['unresolved_ids']:
            if test_report is None:
                raise ValueError('完整叙事包仍有未核对白证据：' + ', '.join(report['unresolved_ids']))
            test_report['unresolved_dialogue_ids'] = report['unresolved_ids']
        if [(r['dialogue_id'], r.get('speaker_id'), r['text']) for r in evidence['dialogues']] != [(r['dialogue_id'], r['speaker_id'], r['text']) for r in narrative['dialogues']]:
            raise ValueError('叙事包与对白审核记录的 ID、说话人、原文或顺序不一致')
    paragraphs = narrative['story_paragraphs']
    by_id = {p['paragraph_id']: p['text'] for p in paragraphs}
    if len(by_id) != len(paragraphs):
        raise ValueError('故事段落编号重复')
    if project_root is not None and narrative.get('human_readable_path'):
        path = project_path(project_root, narrative['human_readable_path'])
        if read_story_paragraphs(path) != paragraphs:
            raise ValueError('结构化段落与编导 Markdown 原文或顺序不一致')
        expected = [(d['dialogue_id'], d['speaker_id'], d['text'], d['source_paragraph_id'])
                    for d in narrative['dialogues']]
        if read_dialogue_table(path) != expected:
            raise ValueError('最终对白表与 JSON 的文字、说话者、顺序或来源段落不一致')
    for collection, key in [('dialogues', 'dialogue_id'), ('asset_requirements', 'asset_id')]:
        if len({item[key] for item in narrative[collection]}) != len(narrative[collection]):
            raise ValueError('重复编号：' + key)
    validate_body_dialogues(narrative)
    if not narrative.get('human_readable_path'):
        for item in narrative['asset_requirements']:
            if not item.get('image_decision') or not str(item.get('image_reason') or '').strip():
                raise ValueError('新 JSON 资产需求必须保留出图决定与依据：' + item['asset_id'])
    for requirement in narrative['asset_requirements']:
        if not set(requirement['source_paragraph_ids']).issubset(by_id):
            raise ValueError('资产引用不存在的故事段落：' + requirement['asset_id'])
    if storyboard is None:
        return test_report
    require_schema(storyboard, 'storyboard-package.schema.json', label='导演包')
    from .quality_evidence import scene_state_requirements
    scene_state_requirements(storyboard)
    if storyboard.get('schema_version') not in {'3.0', '4.0'}:
        raise ValueError('段落引用要求导演包 4.0（3.0 仅历史兼容）')
    if storyboard['project_id'] != narrative['project_id'] or storyboard['source_narrative']['revision'] != narrative['revision']:
        raise ValueError('导演包与编导包项目或修订不匹配')
    all_ids = set(by_id)
    checked_ids = all_ids
    if local_candidate:
        if (not isinstance(candidate_paragraph_ids, list) or not candidate_paragraph_ids
                or any(not isinstance(key, str) for key in candidate_paragraph_ids)
                or len(candidate_paragraph_ids) != len(set(candidate_paragraph_ids))
                or not set(candidate_paragraph_ids).issubset(all_ids)):
            raise ValueError('局部范围须列不重复且存在的故事段落 ID')
        checked_ids = set(candidate_paragraph_ids)
    declared_ids = set(storyboard['source_paragraph_ids'])
    if not (checked_ids <= declared_ids <= all_ids):
        raise ValueError('导演包必须覆盖完整故事段落，不能只覆盖有对白的段落')
    dialogue_ids = {d['dialogue_id'] for d in narrative['dialogues']}
    checked_dialogues = {d['dialogue_id'] for d in narrative['dialogues']
                         if d['source_paragraph_id'] in checked_ids}
    # Image exclusions do not remove story facts. Only active, explicitly approved
    # additions extend the original reference set; use the existing choices reader.
    assets = {a['asset_id']: a for a in narrative['asset_requirements']}
    decisions: dict = {'excluded': {}, 'replaced': {}}
    if project_root is not None:
        if __package__ == 'scripts.core':
            from ..visual_asset_choices import read_choices, effective, confirmed_split_children
        else:
            from visual_asset_choices import read_choices, effective, confirmed_split_children
        choices = read_choices(project_root)
        decisions = choices
        active = effective(narrative, choices)
        require_schema({**narrative, 'asset_requirements': active},
                       'narrative-package.schema.json', label='当前资产需求')
        split_children = confirmed_split_children(project_root, narrative, choices)
        for requirement in active:
            ast = requirement['asset_id']
            if ast in assets:
                continue
            if ast not in split_children and not any(h.get('operation') == 'add' and h.get('ast_id') == ast
                       and h.get('user_confirmed') is True
                       and h.get('source_revision') == narrative['revision']
                       for h in choices.get('history', [])):
                raise ValueError('新增资产缺少当前叙事修订的用户确认：' + ast)
            assets[ast] = requirement
    asset_ids = set(assets)
    if not (checked_dialogues <= set(storyboard['source_dialogue_ids']) <= dialogue_ids):
        raise ValueError('导演包遗漏最终对白或引用未知对白')
    for key, ids in [('source_dialogue_ids', dialogue_ids), ('source_asset_requirement_ids', asset_ids)]:
        if not set(storyboard[key]).issubset(ids):
            raise ValueError('导演包存在未知引用：' + key)
    covered = set()
    covered_dialogues = set()
    shot_ids = []
    shot_sources = {}
    validate_scene_ids(storyboard)
    boundary_ids = set()
    for scene in storyboard['scenes']:
        scene_ids = set(scene['source_paragraph_ids'])
        boundary_ids.update(scene_ids - checked_ids)
        if not scene_ids.issubset(all_ids):
            raise ValueError('场景引用未知段落')
        for item in scene.get('beats', []) + scene['shots']:
            if not set(item['source_paragraph_ids']).issubset(scene_ids):
                raise ValueError('节拍或镜头引用超出场景段落范围')
        for shot in scene['shots']:
            shot_ids.append(shot['shot_id'])
            shot_sources[shot['shot_id']] = set(shot['source_paragraph_ids'])
            if not shot_sources[shot['shot_id']].issubset(checked_ids):
                raise ValueError('镜头跨出本轮校验范围；须纳入完整跨段镜头的全部来源段落：' + shot['shot_id'])
            covered.update(shot['source_paragraph_ids'])
            covered_dialogues.update(shot['source_dialogue_ids'])
            for ast in shot['source_asset_requirement_ids']:
                problem = asset_reference_problem(ast, decisions)
                if problem and problem[0] == 'error':
                    raise ValueError(f"镜头 {shot['shot_id']}：{problem[1]}")
            unknown = [a for a in shot['source_asset_requirement_ids'] if a not in assets]
            if unknown:
                raise ValueError(f"镜头 {shot['shot_id']} 引用未知资产：" + '、'.join(unknown)
                                 + '（当前资产清单里没有该编号：可能已被排除、已拆分或未登记，'
                                   '见 04_资产/当前资产清单.json）')
            for ast in shot['source_asset_requirement_ids']:
                boundary_ids.update(set(assets[ast]['source_paragraph_ids']) - checked_ids)
                if not set(assets[ast]['source_paragraph_ids']) & set(shot['source_paragraph_ids']):
                    raise ValueError('镜头资产与已核对出现段落不对应：' + ast)
            if not set(shot['source_dialogue_ids']).issubset(dialogue_ids):
                raise ValueError('镜头引用未知对白')
            for dialogue in narrative['dialogues']:
                if dialogue['dialogue_id'] in shot['source_dialogue_ids'] and dialogue['source_paragraph_id'] not in shot['source_paragraph_ids']:
                    raise ValueError('镜头对白与所引段落不对应')
    if len(shot_ids) != len(set(shot_ids)):
        raise ValueError('镜头编号重复')
    if covered_dialogues != checked_dialogues:
        raise ValueError('最终对白未完整落实到镜头')
    if covered != checked_ids:
        raise ValueError('有故事段落未落实到镜头；包含无对白动作')
    if storyboard['schema_version'] == '4.0':
        # Fragments may span shots, but must preserve exact text, speaker and order.
        expected = {d['dialogue_id']: d for d in narrative['dialogues']
                    if d['dialogue_id'] in checked_dialogues}
        fragments = {key: [] for key in expected}
        order = []
        for scene in storyboard['scenes']:
            for shot in scene['shots']:
                placements = shot['audio']['dialogue']
                if set(shot['source_dialogue_ids']) != {p['dialogue_id'] for p in placements}:
                    raise ValueError('镜头对白引用与实际承载不一致')
                for placement in placements:
                    key = placement['dialogue_id']
                    if key not in expected or placement['speaker_id'] != expected[key]['speaker_id']:
                        raise ValueError('对白说话者与上游不一致')
                    fragments[key].append(placement['text'])
                    if not order or order[-1] != key:
                        order.append(key)
        if order != list(expected):
            raise ValueError('对白顺序与上游不一致')
        if any(''.join(fragments[key]) != d['text'] for key, d in expected.items()):
            raise ValueError('跨镜对白片段遗漏、重复或改写')
    for gap in storyboard['asset_gaps']:
        boundary_ids.update(set(gap['source_paragraph_ids']) - checked_ids)
        if not set(gap['source_paragraph_ids']).issubset(all_ids):
            raise ValueError('缺口引用未知段落')
        if not set(gap['affected_shot_ids']).issubset(shot_sources):
            raise ValueError('缺口引用未知镜头：' + gap['gap_id'])
        if not set(gap['related_asset_ids']).issubset(asset_ids):
            raise ValueError('缺口引用未知资产：' + gap['gap_id'])
        for shot_id in gap['affected_shot_ids']:
            if not shot_sources[shot_id] & set(gap['source_paragraph_ids']):
                raise ValueError('缺口镜头与来源段落不对应：' + gap['gap_id'] + ' / ' + shot_id)
        for ast in gap['related_asset_ids']:
            if not set(assets[ast]['source_paragraph_ids']) & set(gap['source_paragraph_ids']):
                raise ValueError('缺口资产与来源段落不对应：' + gap['gap_id'] + ' / ' + ast)
    if local_candidate:
        ordered_ids = list(by_id)
        adjacent = {ordered_ids[j] for i, key in enumerate(ordered_ids) if key in checked_ids
                    for j in (i - 1, i + 1) if 0 <= j < len(ordered_ids)
                    and ordered_ids[j] not in checked_ids}
        return {**(test_report or {}),
                'source_narrative_revision': narrative['revision'],
                'source_narrative_canonical_sha256': hashlib.sha256(json.dumps(narrative,
                    ensure_ascii=False, sort_keys=True, separators=(',', ':')).encode()).hexdigest(), 'validation_scope': 'candidate_paragraphs',
                'checked_paragraph_ids': [key for key in by_id if key in checked_ids],
                'unchecked_paragraph_ids': [key for key in by_id if key not in checked_ids],
                'cross_boundary_paragraph_ids': [key for key in by_id if key in boundary_ids],
                'adjacent_unwritten_paragraph_ids': [key for key in by_id if key in adjacent],
                'cross_boundary_semantics_checked': False,
                'whole_package_validated': False, 'formal_adoption_allowed': False,
                'notice': '仅本轮已写段落的结构与来源自查；跨段衔接须人工核对，未写部分未检查，不构成整稿通过或用户确认。'}
    return test_report


def validate_asset_decisions(path: Path) -> None:
    """Check completion of the judgement, not its creative correctness."""
    text = path.read_text(encoding='utf-8-sig')
    header = None
    allowed = {'无需独立图', '复用现有素材', '需要制作', '待定'}
    for line in text.splitlines():
        if not line.strip().startswith('|'):
            header = None
            continue
        cells = [c.strip() for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
        if '资产 ID' in cells or '资产编号' in cells:
            header = cells
            continue
        if not cells or not cells[0].startswith('AST-'):
            continue
        if not header or not {'处理结论', '判断依据'} <= set(header):
            raise ValueError(f'{cells[0]}：资产表缺少处理结论或判断依据列')
        for label in ('处理结论', '判断依据'):
            i = header.index(label)
            if i >= len(cells) or cells[i] in {'', '—', '-', '待补'}:
                raise ValueError(f'{cells[0]}：未完成{label}')
        if cells[header.index('处理结论')] not in allowed:
            raise ValueError(f'{cells[0]}：处理结论不合法')


def validate_candidate(path: Path, baseline: Path | None = None) -> None:
    """Read-only Markdown check before serialization; no approval is inferred."""
    rows = read_dialogue_table(path)
    if len({r[0] for r in rows}) != len(rows):
        raise ValueError('最终对白表编号重复')
    if any(not all(r) for r in rows):
        raise ValueError('完整候选的对白文字、人物和来源段落不能为空')
    assets = []
    text = path.read_text(encoding='utf-8-sig')
    section = re.search(r'^## 资产需求[^\n]*\n(.*?)(?=^## |\Z)', text, re.M | re.S)
    for line in (section[1] if section else '').splitlines():
        cells = [c.strip() for c in re.split(r'(?<!\\)\|', line.strip().strip('|'))]
        if len(cells) >= 3 and cells[1] == 'CHARACTER' and cells[0].startswith('AST-'):
            assets.append({'category': 'CHARACTER', 'entity_id': cells[0].removeprefix('AST-'), 'name': cells[2]})
    narrative = {'story_paragraphs': read_story_paragraphs(path), 'asset_requirements': assets,
                 'dialogues': [dict(zip(('dialogue_id', 'speaker_id', 'text', 'source_paragraph_id'), r)) for r in rows]}
    validate_body_dialogues(narrative)
    if baseline is not None:
        if path.resolve() == baseline.resolve():
            raise ValueError('对白依据不能是正在检查的同一候选')
        expected = read_dialogue_table(baseline)
        if not expected:
            raise ValueError('对白依据缺少有效六列对白表')
        if [r[:3] for r in rows] != [r[:3] for r in expected]:
            raise ValueError('候选与指定对白依据的编号、人物、原句或顺序不一致；核对授权修改后更新依据，不自动采用')


def asset_reference_problem(ast: str, choices: dict) -> tuple[str, str] | None:
    """解释一个资产引用需要提醒的原因：('error'|'warning', 说明)；完全可用时返回 None。

    取消资产图不删除故事物件：已取消或被拆分的资产仍可作为故事事实保留引用，
    因此只告警；真正不可用的是当前任何集合里都没有的编号。
    """
    if ast in choices.get('excluded', {}):
        return ('warning', f'{ast} 已被用户排除出图（state=excluded）：可以保留为故事事实，'
                           '但不得作为素材或出图引用，物件写在镜内过程／动作文字里')
    if ast in choices.get('replaced', {}):
        children = choices['replaced'][ast].get('children', [])
        return ('warning', f'{ast} 已拆分（state=split_parent）：素材与出图引用改用子项'
                           + ('：' + '、'.join(children) if children else ''))
    return None


def validate_shot_plan(path: Path, project_root: Path, strict: bool = False) -> None:
    """导演 Markdown 阶段只读检查：逐镜表「来源」行的资产引用是否仍可用。

    默认只对“当前任何集合都没有的编号”报错；已取消／已拆分的引用按告警输出，
    `strict=True` 时一并报错。
    """
    text = path.read_text(encoding="utf-8-sig")
    if path.suffix.lower() == ".json":
        raise ValueError("--shot-plan 仅检查历史 Markdown 来源行；JSON 请用 --narrative 与 --storyboard")
    if not any(line.lstrip().startswith("| 来源") for line in text.splitlines()):
        raise ValueError("无法检查：未识别历史 Markdown 来源行，不按零行通过；新导演稿请检查原始 JSON")
    from .asset_refs import current_narrative
    if __package__ == 'scripts.core':
        from ..visual_asset_choices import read_choices, effective
    else:
        from visual_asset_choices import read_choices, effective
    choices = read_choices(project_root)
    narrative = current_narrative(project_root)
    known = {a['asset_id'] for a in narrative['asset_requirements']}
    known |= {a['asset_id'] for a in effective(narrative, choices)}
    errors, warnings = [], []
    for number, line in enumerate(text.splitlines(), start=1):
        if not line.lstrip().startswith('| 来源'):
            continue
        for ast in re.findall(r'AST-[A-Z0-9-]+', line):
            problem = asset_reference_problem(ast, choices)
            if problem and problem[0] == 'warning':
                (errors if strict else warnings).append(f'第 {number} 行：{problem[1]}')
                continue
            if ast not in known:
                errors.append(f'第 {number} 行：{ast} 不在当前资产清单中，'
                              '可能已被用户排除、已拆分或从未登记；先核对 04_资产/当前资产清单.json')
    for warning in warnings:
        print('告警：' + warning, file=sys.stderr)
    if errors:
        raise ValueError('镜头资产引用不可用：\n- ' + '\n- '.join(errors))


def _latest_narrative_name(project_root: Path) -> str:
    candidates = sorted((project_root / '02_编导').glob('R*_叙事包.json'))
    if not candidates:
        raise ValueError('找不到叙事包，无法核对资产编号')
    return candidates[-1].name


def reading_supplement(narrative: dict, project_root: Path) -> dict:
    """Omit only text proven identical to Markdown, retain every other field."""
    validate_story_sources(narrative, project_root=project_root)
    if not narrative.get('human_readable_path'):
        return {'kind': 'narrative_reading_supplement',
                'notice': 'JSON 是本修订唯一业务数据源；完整保留原文，不要求 Markdown。此读取不代表用户确认。',
                'narrative': dict(narrative)}
    result = dict(narrative)
    for collection in ('story_paragraphs', 'dialogues'):
        result[collection] = [{k: v for k, v in item.items() if k != 'text'}
                              for item in narrative[collection]]
    return {'kind': 'narrative_reading_supplement',
            'notice': '须完整阅读 human_readable_path 指向的 Markdown；仅省略已校验一致的故事及对白原文，不代表用户确认。此视图不是交付叙事包。',
            'narrative': result}


def export_markdown(package: dict, destination: Path) -> None:
    """Explicit reading export; exclusive create protects existing confirmed files."""
    schema = ('narrative-package.schema.json' if 'story_paragraphs' in package
              else 'storyboard-package.schema.json')
    require_schema(package, schema, label='导出源')
    # Readable projection first; retain every original field in the technical appendix.
    reading = []
    if 'story_paragraphs' in package:
        for paragraph in package['story_paragraphs']:
            reading += ['## ' + paragraph['paragraph_id'], paragraph['text']]
    elif 'scenes' in package:
        for scene in package['scenes']:
            view = director_scene_reading(scene)
            reading += ['## ' + scene['scene_id'], view['body'],
                        '<details' + (' open' if view['technical_open'] else '') + '>',
                        '<summary>逐镜执行附录（同一 JSON）</summary>', '', view['technical'], '', '</details>']
    else:
        reading += ['旧版逐镜阅读（未生成连续正文）', '\n'.join(readable_fields(package))]
    # Fenced JSON retains every field without a second editable business model.
    payload = json.dumps(package, ensure_ascii=False, indent=2)
    fence = '`' * max(3, max((len(m[0]) + 1 for m in re.finditer(r'`+', payload)), default=3))
    text = (f"# {package['project_id']} · {package['revision']}\n\n"
            "本文件是 JSON 的只读阅读导出；修改业务内容请另建 JSON 修订并重新审核确认。\n\n"
            + '\n\n'.join(reading) + '\n\n'
            + '## 完整结构化来源（只读技术附录）\n\n'
            + f"{fence}json\n{payload}\n{fence}\n")
    destination.parent.mkdir(parents=True, exist_ok=True)
    with destination.open('x', encoding='utf-8') as output:
        output.write(text)


def main():
    parser = argparse.ArgumentParser(description='校验编导原文段落及导演引用，不生成或登记产物')
    inputs = parser.add_mutually_exclusive_group(required=True)
    inputs.add_argument('--dialogue-review', type=Path, help='只读核查单份对白审核记录；不代表整包或人工听核')
    inputs.add_argument('--narrative', type=Path)
    inputs.add_argument('--markdown', type=Path)
    inputs.add_argument('--shot-plan', type=Path, help='导演镜头方案 Markdown：只读检查资产引用是否仍有效')
    parser.add_argument('--export-markdown', type=Path, help='按需从已校验 JSON 导出完整阅读视图；拒绝覆盖已有文件')
    parser.add_argument('--dialogue-baseline', type=Path)
    parser.add_argument('--check-asset-decisions', action='store_true', help='检查新稿资产表是否填写判断，不重复语义审查')
    parser.add_argument('--reading-supplement', action='store_true',
                        help='完整校验后只返回 JSON 独有字段及引用，不重复打印 Markdown 原文')
    parser.add_argument('--storyboard', type=Path)
    parser.add_argument('--candidate-paragraph-id', action='append',
                        help='局部候选已写范围，可重复列现有 STP；不放宽正式整稿校验')
    parser.add_argument('--strict-assets', action='store_true',
                        help='把“引用了已取消／已拆分的资产”也当作错误（默认只告警）')
    parser.add_argument('--candidate-source-evidence', help='已登记 source.evidence ID；显式候选正文来源分层，非原声验真或正式批准')
    parser.add_argument('--candidate-test', action='store_true', help='仅隔离候选试跑；保留未核对白并报告生产阻塞')
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args()
    if args.candidate_source_evidence is not None and (
            not args.candidate_test or not args.narrative or args.dialogue_review
            or args.markdown or args.shot_plan or args.reading_supplement or args.dialogue_baseline):
        parser.error('--candidate-source-evidence 仅用于 --narrative --candidate-test 来源检查，不接受其他检查模式')
    if args.candidate_paragraph_id is not None and (not args.narrative or not args.storyboard):
        parser.error('--candidate-paragraph-id 必须与 --narrative 和 --storyboard 使用')
    if args.candidate_test and (not args.narrative or args.reading_supplement):
        parser.error("--candidate-test 仅用于 narrative/storyboard 完整候选校验")
    if args.dialogue_review:
        if args.storyboard or args.export_markdown or args.reading_supplement or args.dialogue_baseline:
            parser.error('--dialogue-review 不接受完整叙事包参数')
        print(json.dumps(validate_dialogue_review(json.loads(args.dialogue_review.read_text(encoding='utf-8-sig')), args.project), ensure_ascii=False, indent=2))
        return
    if args.reading_supplement and (not args.narrative or args.storyboard):
        parser.error('--reading-supplement 仅与 --narrative 使用')
    if args.shot_plan:
        if args.storyboard or args.dialogue_baseline:
            parser.error('--shot-plan 不接受 --storyboard 或 --dialogue-baseline')
        validate_shot_plan(args.shot_plan, args.project, strict=args.strict_assets)
        print('导演稿来源行的资产引用都可追溯；已取消／已拆分的引用按告警列出，拍法与语义仍须自查。')
        return
    if args.markdown:
        if args.storyboard:
            parser.error('--markdown 不接受 --storyboard')
        if args.check_asset_decisions:
            validate_asset_decisions(args.markdown)
        validate_candidate(args.markdown, args.dialogue_baseline)
        print('候选正文与对白表一致；来源真实性、动作因果和确认范围仍须核对。')
        return
    if args.dialogue_baseline:
        parser.error('--dialogue-baseline 仅用于 --markdown')
    narrative = json.loads(args.narrative.read_text(encoding='utf-8-sig'))
    if args.reading_supplement:
        print(json.dumps(reading_supplement(narrative, args.project), ensure_ascii=False, indent=2))
        return
    storyboard = json.loads(args.storyboard.read_text(encoding='utf-8-sig')) if args.storyboard else None
    report = validate_story_sources(narrative, storyboard, project_root=args.project, candidate_test=args.candidate_test,
                                    candidate_paragraph_ids=args.candidate_paragraph_id,
                                    candidate_source_evidence=args.candidate_source_evidence)
    if report is not None:
        print(json.dumps(report, ensure_ascii=False))
    if args.export_markdown:
        export_markdown(storyboard or narrative, args.export_markdown)
    if args.candidate_paragraph_id is not None:
        print('本轮候选范围的来源检查通过；全片未核，跨段语义仍须自查，不可据此正式采用。')
    else:
        print('故事原文与引用一致；此检查不代表语义完整或用户确认。')


if __name__ == '__main__':
    main()
