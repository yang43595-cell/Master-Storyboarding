"""Direct story-text references for new packages; never infer paragraphs from legacy event summaries."""
from __future__ import annotations

import argparse
import json
import re
import sys
from pathlib import Path

from .schema_contracts import require_schema
from .artifacts import project_path


def _dialogue_comparison_text(text):
    """Only whitespace/punctuation; never normalize numerals or meaning."""
    import unicodedata
    return ''.join(c for c in text if not c.isspace() and not unicodedata.category(c).startswith('P'))


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
        refs = row.get('source_ids')
        if (not isinstance(refs, list) or not refs or any(not isinstance(x, str) or x not in sources for x in refs)
                or len(set(refs)) != len(refs) or not isinstance(row.get('text'), str) or not row['text']):
            raise ValueError('对白缺少精确原始证据引用或文字：' + ident)
        originals = [sources[x]['text'] for x in refs]
        if len({_dialogue_comparison_text(t) for t in originals}) > 1:
            conflicts.append(ident)
        proposal = row.get('editorial_proposal')
        if row['text'] not in originals or proposal is not None:
            if (not isinstance(proposal, dict) or proposal.get('proposed_text') != row['text']
                    or proposal.get('original_text') not in originals
                    or not isinstance(proposal.get('reason'), str) or not proposal['reason'].strip()
                    or not isinstance(proposal.get('source_ids'), list) or not proposal['source_ids']
                    or not set(proposal['source_ids']).issubset(refs)
                    or proposal['original_text'] not in [sources[x]['text'] for x in proposal['source_ids']]):
                raise ValueError('解释修订须绑定原文、拟改原句、理由与本行证据：' + ident)
        resolution = row.get('verification')
        if resolution is not None:
            if (not isinstance(resolution, dict) or resolution.get('scope') != 'dialogue'
                    or resolution.get('dialogue_id') != ident or resolution.get('text') != row['text']
                    or resolution.get('method') not in {'human_listening', 'user_confirmation'}
                    or any(not isinstance(resolution.get(k), str) or not resolution[k].strip() for k in ('reference', 'reviewer'))):
                raise ValueError('对白核对记录须绑定本行原文、范围、真实依据与核对者：' + ident)
        else:
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
                verified_times.append(ident)
    return {'verification': 'source_consistency_only', 'conflicts': conflicts,
            'unresolved_ids': unresolved, 'verified_timing_ids': verified_times,
            'limits': 'Hashes and declared provenance do not prove subtitle transcription, listening, identity or approval authenticity.'}


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


def validate_story_sources(narrative: dict, storyboard: dict | None = None, *, project_root: Path | None = None) -> None:
    require_schema(narrative, 'narrative-package.schema.json', label='编导包')
    if narrative.get('schema_version') != '2.0':
        raise ValueError('新段落引用要求编导包 2.0；旧事件包须按原稿重新整理，不能自动换编号')
    if narrative.get('dialogue_evidence_path'):
        if project_root is None:
            raise ValueError('绑定对白审核记录需要 project_root')
        from .artifacts import digest
        evidence_path = project_path(project_root, narrative['dialogue_evidence_path'])
        if digest(evidence_path) != narrative.get('dialogue_evidence_sha256'):
            raise ValueError('对白审核记录已偏离绑定字节')
        evidence = json.loads(evidence_path.read_text(encoding='utf-8-sig'))
        report = validate_dialogue_review(evidence, project_root)
        if report['unresolved_ids']:
            raise ValueError('完整叙事包仍有未核对白证据：' + ', '.join(report['unresolved_ids']))
        if [(r['dialogue_id'], r['text']) for r in evidence['dialogues']] != [(r['dialogue_id'], r['text']) for r in narrative['dialogues']]:
            raise ValueError('叙事包与对白审核记录的 ID、原文或顺序不一致')
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
        return
    require_schema(storyboard, 'storyboard-package.schema.json', label='导演包')
    if storyboard.get('schema_version') not in {'3.0', '4.0'}:
        raise ValueError('段落引用要求导演包 4.0（3.0 仅历史兼容）')
    if storyboard['project_id'] != narrative['project_id'] or storyboard['source_narrative']['revision'] != narrative['revision']:
        raise ValueError('导演包与编导包项目或修订不匹配')
    all_ids = set(by_id)
    if set(storyboard['source_paragraph_ids']) != all_ids:
        raise ValueError('导演包必须覆盖完整故事段落，不能只覆盖有对白的段落')
    dialogue_ids = {d['dialogue_id'] for d in narrative['dialogues']}
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
    if set(storyboard['source_dialogue_ids']) != dialogue_ids:
        raise ValueError('导演包遗漏最终对白或引用未知对白')
    for key, ids in [('source_dialogue_ids', dialogue_ids), ('source_asset_requirement_ids', asset_ids)]:
        if not set(storyboard[key]).issubset(ids):
            raise ValueError('导演包存在未知引用：' + key)
    covered = set()
    covered_dialogues = set()
    shot_ids = []
    for scene in storyboard['scenes']:
        scene_ids = set(scene['source_paragraph_ids'])
        if not scene_ids.issubset(all_ids):
            raise ValueError('场景引用未知段落')
        for item in scene.get('beats', []) + scene['shots']:
            if not set(item['source_paragraph_ids']).issubset(scene_ids):
                raise ValueError('节拍或镜头引用超出场景段落范围')
        for shot in scene['shots']:
            shot_ids.append(shot['shot_id'])
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
                if not set(assets[ast]['source_paragraph_ids']) & set(shot['source_paragraph_ids']):
                    raise ValueError('镜头资产与已核对出现段落不对应：' + ast)
            if not set(shot['source_dialogue_ids']).issubset(dialogue_ids):
                raise ValueError('镜头引用未知对白')
            for dialogue in narrative['dialogues']:
                if dialogue['dialogue_id'] in shot['source_dialogue_ids'] and dialogue['source_paragraph_id'] not in shot['source_paragraph_ids']:
                    raise ValueError('镜头对白与所引段落不对应')
    if len(shot_ids) != len(set(shot_ids)):
        raise ValueError('镜头编号重复')
    if covered_dialogues != dialogue_ids:
        raise ValueError('最终对白未完整落实到镜头')
    if covered != all_ids:
        raise ValueError('有故事段落未落实到镜头；包含无对白动作')
    if storyboard['schema_version'] == '4.0':
        # Fragments may span shots, but must preserve exact text, speaker and order.
        expected = {d['dialogue_id']: d for d in narrative['dialogues']}
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
        if not set(gap['source_paragraph_ids']).issubset(all_ids):
            raise ValueError('缺口引用未知段落')


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
    from .asset_refs import current_narrative
    if __package__ == 'scripts.core':
        from ..visual_asset_choices import read_choices, effective
    else:
        from visual_asset_choices import read_choices, effective
    choices = read_choices(project_root)
    narrative = current_narrative(project_root)
    known = {a['asset_id'] for a in narrative['asset_requirements']}
    known |= {a['asset_id'] for a in effective(narrative, choices)}
    text = path.read_text(encoding='utf-8-sig')
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
    # Fenced JSON retains every field without a second editable business model.
    payload = json.dumps(package, ensure_ascii=False, indent=2)
    fence = '`' * max(3, max((len(m[0]) + 1 for m in re.finditer(r'`+', payload)), default=3))
    text = (f"# {package['project_id']} · {package['revision']}\n\n"
            "本文件是 JSON 的只读阅读导出；修改业务内容请另建 JSON 修订并重新审核确认。\n\n"
            f"{fence}json\n{payload}\n{fence}\n")
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
    parser.add_argument('--strict-assets', action='store_true',
                        help='把“引用了已取消／已拆分的资产”也当作错误（默认只告警）')
    parser.add_argument('--project', type=Path, required=True)
    args = parser.parse_args()
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
    validate_story_sources(narrative, storyboard, project_root=args.project)
    if args.export_markdown:
        export_markdown(storyboard or narrative, args.export_markdown)
    print('故事原文与引用一致；此检查不代表语义完整或用户确认。')


if __name__ == '__main__':
    main()
