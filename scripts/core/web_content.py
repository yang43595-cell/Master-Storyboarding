"""Pure canonical-JSON → immutable website view compilation; never publishes."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path, PureWindowsPath
from .prompt_body import submitted_prompt_body
from .schema_contracts import require_schema
from .story_sources import validate_body_dialogues
from .artifacts import project_path
from .director_reading import director_scene_reading, TECHNICAL_MARKER

ROOT = Path(__file__).resolve().parents[2]
CONTRACT = ROOT / 'references/web-contract-v78'
HASH = re.compile(r'^[a-f0-9]{64}$')
# Existing website FeedbackRequest.id (pinned content-contract.ts), not a new ID format.
FEEDBACK_ID = re.compile(r'[A-Za-z0-9_-]{8,100}')


class WebContentError(ValueError):
    pass


def canonical(value):
    return json.dumps(value, ensure_ascii=False, sort_keys=True, separators=(',', ':'), allow_nan=False)


def stable_id(value, prefix):
    return value if re.fullmatch(r'[A-Za-z0-9_-]{1,120}', value) else prefix + '-' + hashlib.sha256(value.encode()).hexdigest()[:24]


def prompt_segment_views(business, *, source_sha256, transport_evidence):
    """Read current segment JSON and supplied native readback without mutating either.

    This checks transport identity, not media bytes, website publication or approval.
    Labels that differ from source IDs must be explicitly supplied, never guessed.
    """
    def fail(reason):
        raise WebContentError('视频分段适配：' + reason)
    def nonempty(value):
        return isinstance(value, str) and bool(value.strip())
    sections = business.get('creative_sections')
    links = None
    if 'generation_segments' in business:
        segments = business['generation_segments']
        if isinstance(sections, list):
            if any(not isinstance(s, dict) or not isinstance(s.get('generation_segments'), list) for s in sections):
                fail('创作段生成段引用列表不完整')
            links = [ident for section in sections for ident in section.get('generation_segments', [])]
    elif isinstance(sections, list) and sections:
        if any(not isinstance(s, dict) or not isinstance(s.get('generation_segments'), list)
               or not s['generation_segments'] for s in sections):
            fail('创作段缺少完整 generation_segments')
        segments = [segment for section in sections for segment in section['generation_segments']]
    else:
        fail('缺少现行完整 drafts / generation_segments；不恢复退役 production_plan')
    if (not isinstance(segments, list) or not segments
            or any(not isinstance(s, dict) or not nonempty(s.get('segment_id')) for s in segments)):
        fail('生成段列表或 segment_id 不完整')
    ids = [s['segment_id'] for s in segments]
    if len(set(ids)) != len(ids):
        fail('segment_id 重复')
    if links is not None and (any(not isinstance(ident, str) for ident in links) or set(links) != set(ids)):
        fail('顶层生成段与创作段引用不一致，不猜哪份为准')
    if not isinstance(transport_evidence, dict) or transport_evidence.get('source_sha256') != source_sha256:
        fail('缺少绑定准确源字节的 transport_evidence；不补造 native node_id/media_id')
    rows = transport_evidence.get('native_readback')
    if (not isinstance(rows, list) or any(not isinstance(r, dict) for r in rows)
            or [r.get('segment_id') for r in rows] != ids):
        fail('原生读回须按源 segment_id 顺序完整覆盖，不遗漏或重用其他段')
    labels = transport_evidence.get('input_labels', {})
    if not isinstance(labels, dict) or any(not nonempty(k) or not nonempty(v) for k, v in labels.items()):
        fail('input_labels 必须是准确源输入 ID 到实际原生标签的映射')
    platform = business.get('platform', {})
    if not isinstance(platform, dict):
        fail('platform 参数必须是对象')
    views, used_labels = [], set()
    for segment, row in zip(segments, rows):
        ident = segment['segment_id']
        try:
            prompt = submitted_prompt_body(segment)
        except ValueError as error:
            fail(ident + ' ' + str(error))
        if not nonempty(prompt) or not nonempty(row.get('prompt')):
            fail(ident + ' 缺完整提示词正文')
        # Canvas readback changes whitespace around paragraphs/chips, not words.
        if ''.join(prompt.split()) not in ''.join(row['prompt'].split()):
            fail(ident + ' 原生读回不含完整源正文（仅忽略空白排版）')
        if row.get('refreshed') is not True or row.get('model_visible') is not True:
            fail(ident + ' 缺刷新后原生读回/模型控件证据')
        nodes = row.get('nodes')
        pattern = r'(?<![A-Za-z0-9_-])' + re.escape(ident) + r'(?![A-Za-z0-9_-])'
        node_ids = {n.get('id') for n in nodes if isinstance(n, dict)
                    and nonempty(n.get('id')) and isinstance(n.get('text'), str)
                    and re.search(pattern, n['text'])} if isinstance(nodes, list) else set()
        if len(node_ids) != 1:
            fail(ident + ' 缺少唯一原生草稿节点证据，不合成 ID')
        parameters = segment.get('parameters')
        if parameters is None:
            parameters = {'model': platform.get('model'),
                          'reference_mode': platform.get('reference_mode', platform.get('mode')),
                          'ratio': platform.get('aspect_ratio'), 'resolution': platform.get('resolution'),
                          'duration_seconds': segment.get('duration_seconds')}
        if (not isinstance(parameters, dict)
                or any(not nonempty(parameters.get(k)) for k in ('model', 'reference_mode', 'ratio', 'resolution'))
                or type(parameters.get('duration_seconds')) is not int or parameters['duration_seconds'] <= 0):
            fail(ident + ' 缺准确源参数')
        if any(segment[k] != parameters['duration_seconds'] for k in ('duration_seconds', 'generation_seconds') if k in segment):
            fail(ident + ' 源生成时长与参数不一致')
        controls = row.get('parameter_controls')
        texts = [c.get('text') for c in controls if isinstance(c, dict)] if isinstance(controls, list) else []
        if (len(texts) != 4 or not isinstance(texts[0], str)
                or texts[0].split('（', 1)[0] != parameters['ratio']
                or texts[1:] != [parameters['resolution'], str(parameters['duration_seconds']) + 's', parameters['reference_mode']]):
            fail(ident + ' 原生比例/分辨率/时长/参考方式与源参数不一致')
        materials = segment.get('materials', segment.get('candidate_upload_order'))
        if segment.get('actual_uploaded_materials'):
            fail(ident + ' actual_uploaded_materials 非空，须核对实际输入而非忽略')
        refs = row.get('references')
        if (not isinstance(materials, list) or not isinstance(refs, list) or len(materials) != len(refs)
                or any(not isinstance(m, dict) for m in materials)
                or any(not isinstance(r, dict) or any(not nonempty(r.get(k)) for k in ('id', 'material_id', 'label')) for r in refs)
                or row.get('chip_order') != [r['id'] for r in refs]):
            fail(ident + ' 实际输入、原生媒体身份或 chip 顺序不完整')
        inputs = []
        for index, (material, ref) in enumerate(zip(materials, refs), 1):
            key = material.get('reference_id', material.get('material_id'))
            path = material.get('path', material.get('file'))
            sha = material.get('sha256')
            role = material.get('responsibility', material.get('primary_role'))
            order = material.get('order', material.get('upload_index'))
            if (not nonempty(key) or not nonempty(path) or not nonempty(role)
                    or not isinstance(sha, str) or not HASH.fullmatch(sha)
                    or type(order) is not int or order != index):
                fail(ident + ' 素材来源、职责、hash 或输入顺序不完整')
            if ref['label'] != labels.get(key, key):
                fail(ident + ' 第' + str(index) + '项原生标签与源输入不一致')
            if key in labels:
                used_labels.add(key)
            inputs.append({'key': key + '@' + sha, 'path': path, 'sha256': sha,
                           'source': material, 'native': {'node_id': ref['id'], 'media_id': ref['material_id']}})
        views.append({'segment': segment, 'prompt': prompt, 'parameters': parameters, 'inputs': inputs,
                      'node_id': next(iter(node_ids))})
    if set(labels) != used_labels:
        fail('input_labels 含未使用或其他源输入的对应')
    if len({v['node_id'] for v in views}) != len(views):
        fail('不同生成段指向同一原生草稿节点')
    return views


def source_review_segments(business):
    """Read existing source layouts only; do not infer execution or approval."""
    if 'drafts' in business:
        raise WebContentError('源稿审阅须输入原业务生成段，不以 drafts 传输快照替代')
    sections = business.get('creative_sections')
    if 'generation_segments' in business:
        segments = business['generation_segments']
    elif isinstance(sections, list) and sections and all(
            isinstance(s, dict) and isinstance(s.get('generation_segments'), list) for s in sections):
        segments = [row for section in sections for row in section['generation_segments']]
    else:
        raise WebContentError('源稿审阅缺少完整 generation_segments')
    if not isinstance(segments, list) or not segments or any(
            not isinstance(row, dict) or not isinstance(row.get('segment_id'), str)
            or not row['segment_id'].strip() for row in segments):
        raise WebContentError('源稿审阅生成段或 segment_id 不完整')
    ids = [row['segment_id'] for row in segments]
    if len(set(ids)) != len(ids):
        raise WebContentError('源稿审阅 segment_id 重复')
    if 'generation_segments' in business and sections is not None:
        if (not isinstance(sections, list) or any(not isinstance(s, dict)
                or not isinstance(s.get('generation_segments'), list) for s in sections)):
            raise WebContentError('源稿审阅创作段引用格式不完整')
        links = [ident for section in sections for ident in section['generation_segments']]
        if any(not isinstance(ident, str) for ident in links) or set(links) != set(ids):
            raise WebContentError('源稿审阅顶层生成段与创作段引用不一致')
    return segments


def validate_web_request(request, *, contract_root=CONTRACT):
    """Use the actual executable TS schema, not a Python copy of its rules."""
    root = Path(contract_root).resolve()
    command = ['node', str(ROOT / 'scripts/validate_web_content.mjs')]
    result = subprocess.run(command, input=canonical({'contractRoot': str(root), 'request': request}),
                            text=True, capture_output=True, timeout=30)
    if result.returncode:
        raise WebContentError('网页原始 schema 校验失败：' + result.stderr[:6000])
    output = json.loads(result.stdout)
    if len(canonical(output['request']).encode()) > 1024 * 1024:
        raise WebContentError('请求超过真实网页 1 MiB 上限；不截断来源')
    output['contractHashes'] = {n: hashlib.sha256((root / n).read_bytes()).hexdigest()
                                for n in ['lib/content-contract.ts','lib/package-metadata.cjs']}
    return output


def compile_content(raw: bytes, *, stage: str, case_id: str, source_script_version: str,
                    expected_version: str | None, scope: str, media_registry: dict | None = None,
                    benchmark: dict | None = None, title: str | None = None,
                    reviewable: bool = False, contract_root=CONTRACT, project_root: Path | None = None,
                    transport_evidence: dict | None = None, prompt_mode: str = "executed",
                    director_narrative: dict | None = None, candidate_paragraph_ids: list[str] | None = None):
    """Compile from one source plus explicit transport/readback evidence.

    media_registry contains existing media descriptors/local paths and the actual
    publish helper's per-media readback receipt; it is not another business draft.
    Synthetic receipts belong only in tests. No URL/hash/source basis is guessed.
    """
    if candidate_paragraph_ids is not None and (stage != 'director' or reviewable or director_narrative is None):
        raise WebContentError('局部导演送审须提供完整原叙事及 STP 范围，且 reviewable 必须为 false')
    if director_narrative is not None and stage != 'director':
        raise WebContentError('director_narrative 仅用于导演来源自查')
    director_report = None
    media_registry = {} if media_registry is None else media_registry
    if not isinstance(media_registry, dict):
        raise WebContentError('media_registry 必须是对象')
    if prompt_mode not in ('executed', 'source_review'):
        raise WebContentError('未知 prompt_mode')
    if prompt_mode == 'source_review' and (stage != 'prompts' or reviewable or transport_evidence is not None):
        raise WebContentError('source_review 仅供 prompts 未确认只读审阅；不得设为可通过或附已执行传输证据')
    if (not isinstance(source_script_version, str) or not HASH.fullmatch(source_script_version)
            or (expected_version is not None and (not isinstance(expected_version, str) or not HASH.fullmatch(expected_version)))):
        raise WebContentError('必须显式提供真实 sourceScriptVersion 与 expectedVersion（首包才用 null）')
    business = json.loads(raw)
    if not isinstance(business, dict) or not business.get('revision'):
        raise WebContentError('业务 JSON 必须有准确 revision')
    native_hash = hashlib.sha256(canonical(business).encode()).hexdigest()
    blocks = []
    compiled_media_map = []
    def block(ident, heading, text, media=None):
        # JS schema counts UTF-16 units. At most 24k Unicode codepoints
        # keeps each generated view below 50k units without truncating text.
        chunks = [text[i:i+24000] for i in range(0, len(text), 24000)] or ['']
        first = None
        for i, chunk in enumerate(chunks):
            item = {'id': stable_id(ident if i == 0 else ident + '-part-' + str(i+1), 'block'),
                    'heading': heading if i == 0 else heading + '（续' + str(i+1) + '）',
                    'text': chunk, 'media': (media or []) if i == 0 else []}
            blocks.append(item)
            if first is None:
                first = item
        return first
    anchor_counts = {}
    def reference_anchor(scope, identity):
        base = scope + '-' + hashlib.sha256(canonical(identity).encode()).hexdigest()[:24]
        occurrence = anchor_counts.get(base, 0)
        anchor_counts[base] = occurrence + 1
        return base if occurrence == 0 else base + '-repeat-' + str(occurrence+1)
    def media(key, anchor, expected_hash=None, expected_path=None):
        record = media_registry.get(key)
        if not isinstance(record, dict):
            raise WebContentError('缺少实际不可变媒体/回读映射：' + key)
        witness = record.get('readback', {})
        if (not isinstance(record.get('id'), str) or not record['id'].strip()
                or not isinstance(witness, dict) or not isinstance(witness.get('mediaVerified'), str)
                or not witness['mediaVerified'].strip()):
            raise WebContentError('缺少实际媒体标识或回读标识：' + key)
        fields = ('url','sha256','mime','bytes')
        if any(k not in record for k in fields) or not record.get('localPath'):
            raise WebContentError('媒体元数据不完整：' + key)
        path = Path(record['localPath'])
        if not path.is_file():
            raise WebContentError('媒体本地字节不存在：' + key)
        with path.open('rb') as stream:
            actual_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
        if expected_path is not None:
            source_path = Path(expected_path)
            if not source_path.is_absolute() and not PureWindowsPath(expected_path).is_absolute() and project_root is None:
                raise WebContentError('相对资产来源需要明确 project_root：' + str(expected_path))
            source_path = project_path(Path(project_root) if project_root is not None else Path.cwd(), expected_path)
            if not source_path.is_file():
                raise WebContentError('业务 JSON 登记的媒体来源不存在：' + str(expected_path))
            with source_path.open('rb') as stream:
                source_sha = hashlib.file_digest(stream, 'sha256').hexdigest()
            if source_sha != actual_sha or source_path.stat().st_size != path.stat().st_size:
                raise WebContentError('业务来源路径与 registry 媒体字节不一致：' + key)
        if record['mime'].startswith('image/'):
            from PIL import Image
            with Image.open(path) as image:
                expected_mime = {'PNG':'image/png','JPEG':'image/jpeg','WEBP':'image/webp'}.get(image.format)
                image.verify()
            if expected_mime != record['mime']:
                raise WebContentError('媒体 MIME 与实际图片不一致：' + key)
        elif record['mime'] == 'video/mp4':
            with path.open('rb') as stream:
                if stream.read(8)[4:8] != b'ftyp':
                    raise WebContentError('媒体 MIME 与实际视频不一致：' + key)
        if (path.stat().st_size != record['bytes']
                or actual_sha != record['sha256']
                or (expected_hash is not None and expected_hash != record['sha256'])
                or witness.get('mediaVerified') != record.get('id')
                or witness.get('sha256') != record['sha256'] or witness.get('bytes') != record['bytes']):
            raise WebContentError('媒体字节或既有回读凭据不一致：' + key)
        item = {'id': stable_id(anchor, 'media'), **{k: record[k] for k in fields},
                'caption': record.get('caption', '')}
        compiled_media_map.append({'id':item['id'], 'localPath':record['localPath'],
                                   **{k:record[k] for k in ('sha256','mime','bytes')}})
        return item
    def add_benchmark():
        if not benchmark:
            raise WebContentError('bound script 缺少真实 benchmark 原视频/海报及来源片段；不补造')
        meta = {k: v for k, v in benchmark.items() if k not in {'videoKey','posterKey'}}
        video = media(benchmark['videoKey'], meta['videoMediaId'])
        poster = media(benchmark['posterKey'], meta['posterMediaId'])
        block('benchmark-reference', '对标原视频', canonical(meta), [video, poster])
    if stage == 'script':
        if business.get('schema_version') != '2.0':
            raise WebContentError('编导适配仅支持 narrative 2.0；旧版 legacy v1 须按真实正文整理新修订，不猜段落')
        require_schema(business, 'narrative-package.schema.json', label='网页编导源')
        validate_body_dialogues(business)
        # The original renderer recognizes only numeric STP headings. If any
        # valid source ID is outside that subset, use its generic main-scene
        # view for the entire story. Never renumber IDs or leave a paragraph
        # in extras merely because other paragraphs matched the numeric view.
        numeric_view = all(re.match(r'^STP-(?:C\d+-)?\d+', p['paragraph_id'])
                           for p in business['story_paragraphs'])
        for paragraph in business['story_paragraphs']:
            heading = (paragraph['paragraph_id'] + '｜故事正文' if numeric_view
                       else '正文段落｜' + paragraph['paragraph_id'])
            block(paragraph['paragraph_id'], heading, paragraph['text'])
        add_benchmark()
        if business['open_questions'] and reviewable:
            raise WebContentError('未决事实尚在，不能把本包设为可通过')
    elif stage == 'director':
        require_schema(business, 'storyboard-package.schema.json', label='网页导演源')
        from .story_sources import validate_scene_ids, validate_story_sources
        validate_scene_ids(business)
        if director_narrative is not None:
            director_report = validate_story_sources(director_narrative, business,
                project_root=Path(project_root) if project_root is not None else None,
                candidate_paragraph_ids=candidate_paragraph_ids)
        if director_report is not None:
            block('director-validation-scope', '本轮来源自查范围（非整稿通过）', canonical(director_report))
        if business['schema_version'] != '4.0':
            raise WebContentError('当前导演适配只支持已核 v4 JSON，旧格式不猜拍法')
        for scene in business['scenes']:
            view = director_scene_reading(scene)
            # Preserve scene IDs and the full canonical source. The paired reading
            # renderer folds only this explicitly delimited technical appendix.
            block(scene['scene_id'], scene['scene_id'] + ' 导演视听展开',
                  view['body'] + '\n\n' + TECHNICAL_MARKER + '\n\n' + view['technical'])
    elif stage == 'assets':
        require_schema(business, 'visual-asset-manifest.schema.json', label='网页资产源')
        groups = []
        kinds = {'CHARACTER': 'character','WARDROBE':'wardrobe','LOCATION':'setting','REAL_PRODUCT':'product'}
        blockers = [gap['reason'] for gap in business['scope_gaps']]
        for asset in business['assets']:
            if asset['candidates'] or not asset.get('confirmed_outputs'):
                raise WebContentError('资产缺少逐输出职责/用途，r3 不能代替 assets-v2：' + asset['asset_id'])
            b = block(asset['asset_id'], asset['name'], '来源范围：' + canonical(asset['paragraph_scope']))
            items = []
            for output in asset['confirmed_outputs']:
                key = asset['asset_id'] + '@' + output['output_id']
                m = media(key, key, expected_path=output['path'])
                b['media'].append(m)
                items.append({'blockId': b['id'], 'mediaId': m['id'], 'title': '资产：' + asset['name'] + ' · ' + output['view'],
                              'role': '用途：' + output['purpose'], 'origin': 'reference', 'status': 'reference',
                              'usageScope': '登记段落：' + '、'.join(asset['paragraph_scope']) + '；登记镜头：' + '、'.join(asset['shot_scope']) + '；状态：' + output['state'],
                              'exclusions': asset['must_not_inherit']})
            groups.append({'id': b['id'], 'kind': kinds.get(asset['category'],'reference'), 'title': '资产：' + asset['name'], 'items': items})
        if blockers and reviewable:
            raise WebContentError('资产缺口未闭合，不能设为可通过')
        block('asset-layout', '资产展示分组', canonical({'schemaVersion':1,'groups':groups,'blockers':blockers}))
        if benchmark:
            add_benchmark()
    elif stage == 'prompts' and prompt_mode == 'source_review':
        notice = '候选源稿只读审阅，不构成用户确认。画布绑定、生成及网页媒体挂接未验证；不代表已执行或可提交。'
        block('source-review-status', '源稿审阅状态', notice)
        missing_media = []
        segments = source_review_segments(business)
        for segment in segments:
            ident = segment['segment_id']
            try:
                prompt = submitted_prompt_body(segment)
            except ValueError as error:
                raise WebContentError(ident + ' ' + str(error)) from error
            if not prompt:
                raise WebContentError(ident + ' 缺完整提示词正文')
            materials = segment.get('materials', segment.get('candidate_upload_order'))
            if materials is not None and (not isinstance(materials, list)
                    or any(not isinstance(m, dict) for m in materials)):
                raise WebContentError(ident + ' 素材清单格式无法识别')
            refs, missing = [], []
            for index, material in enumerate(materials or [], 1):
                key = material.get('reference_id', material.get('material_id', material.get('key')))
                sha = material.get('sha256')
                registry_key = key + '@' + sha if isinstance(key, str) and isinstance(sha, str) else None
                if registry_key is not None and registry_key in media_registry:
                    path = material.get('path', material.get('file'))
                    if not isinstance(path, str) or not path or not HASH.fullmatch(sha):
                        raise WebContentError(ident + ' 网页素材缺准确源路径或 hash')
                    refs.append(media(registry_key, reference_anchor(ident, {'source': registry_key}),
                                      sha, expected_path=path))
                else:
                    missing.append(str(index) + '：' + (key if isinstance(key, str) else '源标识缺失'))
            missing_media.append({'segment_id': ident, 'items': missing,
                                  'source_materials_missing': materials is None})
            status = ('素材清单缺失；未挂图' if materials is None else
                      '未挂图素材：' + '、'.join(missing) if missing else
                      '素材清单为空；无挂图' if not materials else '所列网页媒体已有字节及回执校验；不证明画布绑定或生成')
            block(ident, '候选源稿：' + ident,
                  prompt + '\n\n原稿参数（未补默认值）：' + canonical(segment.get('parameters'))
                  + '\n\n原稿素材及完整职责：' + canonical(materials)
                  + '\n\n媒体状态：' + status + '\n' + notice, refs)
    elif stage == 'prompts':
        if 'drafts' in business and ('generation_segments' in business or 'creative_sections' in business):
            raise WebContentError('视频 JSON 同时存在 drafts 与生成段，不猜权威正文')
        if 'drafts' not in business:
            views = prompt_segment_views(business, source_sha256=hashlib.sha256(raw).hexdigest(),
                                         transport_evidence=transport_evidence)
            if reviewable and (business.get('production_blocked') is True
                    or business.get('user_approved') is False or business.get('status') == 'candidate'
                    or business.get('review_status') == 'unreviewed'
                    or any(v['segment'].get('submit_ready') is False or v['segment'].get('production_blocked') is True
                           or v['segment'].get('status') == 'candidate' or v['segment'].get('review_status') == 'unreviewed'
                           or v['segment'].get('user_approved') is False
                           or v['segment'].get('blockers') or v['segment'].get('open_gaps') for v in views)):
                raise WebContentError('候选/生产阻塞或未决事实尚在，不能设为可通过')
            for view in views:
                segment = view['segment']
                refs = [media(ref['key'], reference_anchor(segment['segment_id'],
                              {'source': ref['key'], 'native': ref['native']}),
                              ref['sha256'], expected_path=ref['path']) for ref in view['inputs']]
                block(segment['segment_id'], '视频段：' + segment['segment_id'],
                      view['prompt'] + '\n\n参数：' + canonical(view['parameters'])
                      + '\n\n素材职责与候选状态（源字段，只读）：' + canonical(segment), refs)
        elif not isinstance(business['drafts'], list) or not business['drafts']:
            raise WebContentError('缺少现行完整视频 drafts JSON；不恢复退役 production_plan')
        for draft in business.get('drafts', []):
            ordered, checks = draft.get('ordered_media'), draft.get('reference_checks')
            if not isinstance(ordered, list) or not isinstance(checks, list) or len(ordered) != len(checks):
                raise WebContentError('视频素材职责不能遗漏实际输入')
            if any(not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                   for k in ('node_id','media_id')) for item in ordered):
                raise WebContentError('视频实际输入缺少准确 native node_id/media_id')
            refs = [media(ref['artifact_id'] + '@' + ref['revision'],
                          reference_anchor(draft['node_id'], {'reference':ref,'native':native}), ref['sha256'])
                    for native, ref in zip(ordered, checks)]
            block(draft['node_id'], '视频段：' + draft['node_id'], draft['prompt'] + '\n\n参数：' + canonical(draft['parameters']), refs)
    elif stage in {'white_model','storyboard'}:
        if not business.get('files'):
            raise WebContentError('缺少已登记预览 files；不能从 assets-v2 缺失路径补造媒体')
        refs = [media(f['path'], reference_anchor('preview', f), f.get('sha256'), expected_path=f['path']) for f in business['files']]
        block('reference-views', '已登记参考视图', '仅展示已有登记，不运行白模或生成图片。', refs)
        if reviewable:
            raise WebContentError('此历史 JSON 仅能提供参考视图；完整 assets-v2 适配未恢复')
    else:
        raise WebContentError('不支持的网页阶段：' + stage)
    # Generated immutable provenance, never another editable source. Retains all
    # fields so downstream metadata or unknown real facts cannot be silently lost.
    block('canonical-source', '结构化来源（只读）', canonical(business))
    block('source-binding', '来源版本', canonical({'schemaVersion':1,
          'source_sha256':hashlib.sha256(raw).hexdigest(), 'native_sha256':native_hash}))
    content = {'schemaVersion':1, 'caseId':case_id, 'stage':stage, 'revision':business['revision'],
               'sourceScriptVersion':source_script_version, 'title':title or ('阶段内容 · ' + business.get('project_id',case_id)),
               'summary':'由同一业务 JSON 编译；不创建批准或生成授权。', 'reviewable':reviewable, 'blocks':blocks}
    validated = validate_web_request({'scope':scope,'expectedVersion':expected_version,'content':content}, contract_root=contract_root)
    validated['mediaMap'] = compiled_media_map
    validated['remoteReadbackPerformed'] = False
    validated['published'] = False
    if director_report is not None:
        validated['directorSourceCheck'] = director_report
    if stage == 'prompts' and prompt_mode == 'executed' and 'drafts' not in business:
        validated['transportEvidence'] = {'source_sha256': hashlib.sha256(raw).hexdigest(),
            'native_readback_canonical_sha256': hashlib.sha256(canonical(transport_evidence['native_readback']).encode()).hexdigest(),
            'segmentCount': len(views), 'inputCount': sum(len(v['inputs']) for v in views),
            'modelSelectionVerified': False}
    if prompt_mode == 'source_review':
        validated['sourceReview'] = {'mode': 'source_review', 'userConfirmation': 'not_established',
            'productionBlocked': True,
            'canvasBindingVerified': False, 'generationVerified': False,
            'segmentCount': len(segments), 'unattachedMedia': missing_media}
    return validated


def effective_feedback(response: dict, *, scope: str, case_id: str, stage: str, version: str):
    """Consume authoritative current state; never infer from default approval/history."""
    state = response.get('currentState')
    if not isinstance(state, dict):
        return {'effectiveApproval':'unknown', 'currentFeedback':None, 'reason':'missing currentState'}
    for key, expected in [('scope',scope),('caseId',case_id),('stage',stage),('version',version)]:
        if state.get(key) != expected:
            raise WebContentError('当前网页反馈身份不匹配：' + key)
    feedback = state.get('currentFeedback')
    if 'currentFeedback' in response and feedback != response['currentFeedback']:
        raise WebContentError('currentState 与 currentFeedback 不一致')
    if feedback is not None:
        if not isinstance(feedback, dict):
            raise WebContentError('当前反馈回执必须是对象')
        ident = feedback.get('id')
        if not isinstance(ident, str) or not FEEDBACK_ID.fullmatch(ident):
            raise WebContentError('当前反馈回执 id 不符合网页 FeedbackRequest 契约')
        for key, expected in [('scope',scope),('caseId',case_id),('stage',stage),('version',version)]:
            if feedback.get(key) != expected:
                raise WebContentError('反馈不属于本次准确版本：' + key)
    required = {'isCurrent','currentVersion','sourceStale','reviewable','currentFeedback',
                'sourceScriptVersion','packageSourceScriptVersion'}
    if not required <= state.keys():
        approval = 'unknown'
    elif state['isCurrent'] is not True or state['currentVersion'] != version:
        approval = 'historical'
    elif (state['sourceStale'] is not False or not HASH.fullmatch(str(state['sourceScriptVersion']))
          or not HASH.fullmatch(str(state['packageSourceScriptVersion']))
          or (stage != 'script' and state['sourceScriptVersion'] != state['packageSourceScriptVersion'])):
        approval = 'stale'
    elif state['reviewable'] is not True:
        approval = 'not_reviewable'
    elif feedback and feedback.get('decision') == 'changes':
        approval = 'changes_requested'
    elif feedback and feedback.get('decision') == 'approve':
        approval = 'approved'
    elif feedback is None:
        approval = 'pending'
    else:
        approval = 'unknown'
    return {'effectiveApproval':approval, 'currentFeedback':copy.deepcopy(feedback),
            'historyComplete': not response['feedbackPage']['hasMore'] if isinstance(response.get('feedbackPage'),dict) and 'hasMore' in response['feedbackPage'] else None}


def main():
    """Read a source path and existing transport evidence from stdin; stdout only."""
    import sys
    try:
        job = json.load(sys.stdin)
        result = compile_content(Path(job['sourcePath']).read_bytes(), stage=job['stage'],
            case_id=job['caseId'], source_script_version=job['sourceScriptVersion'],
            expected_version=job['expectedVersion'], scope=job['scope'], media_registry=job.get('mediaRegistry'),
            benchmark=job.get('benchmark'), title=job.get('title'), reviewable=job.get('reviewable', False),
            contract_root=job.get('contractRoot', CONTRACT), project_root=job.get('projectRoot'),
            transport_evidence=job.get('transportEvidence'), prompt_mode=job.get('promptMode', 'executed'),
            director_narrative=(json.loads(Path(job['directorNarrativePath']).read_text(encoding='utf-8-sig'))
                                if job.get('directorNarrativePath') else None),
            candidate_paragraph_ids=job.get('candidateParagraphIds'))
        print(canonical(result))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(canonical({'ok':False,'published':False,'error':str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
