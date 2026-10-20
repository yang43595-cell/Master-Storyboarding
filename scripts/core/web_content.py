"""Pure canonical-JSON → immutable website view compilation; never publishes."""
from __future__ import annotations
import copy
import hashlib
import json
import re
import subprocess
from pathlib import Path, PureWindowsPath
from .schema_contracts import require_schema
from .story_sources import validate_body_dialogues
from .artifacts import project_path

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
                    expected_version: str | None, scope: str, media_registry: dict,
                    benchmark: dict | None = None, title: str | None = None,
                    reviewable: bool = False, contract_root=CONTRACT, project_root: Path | None = None):
    """Compile from one source plus explicit transport/readback evidence.

    media_registry contains existing media descriptors/local paths and the actual
    publish helper's per-media readback receipt; it is not another business draft.
    Synthetic receipts belong only in tests. No URL/hash/source basis is guessed.
    """
    if (not isinstance(source_script_version, str) or not HASH.fullmatch(source_script_version)
            or (expected_version is not None and (not isinstance(expected_version, str) or not HASH.fullmatch(expected_version)))):
        raise WebContentError('必须显式提供真实 sourceScriptVersion 与 expectedVersion（首包才用 null）')
    business = json.loads(raw)
    if not isinstance(business, dict) or not business.get('revision'):
        raise WebContentError('业务 JSON 必须有准确 revision')
    if not isinstance(media_registry, dict):
        raise WebContentError('mediaRegistry 必须是媒体映射对象')
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
        if not isinstance(record['mime'], str):
            raise WebContentError('媒体 mime 必须是字符串：' + key)
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
        if not isinstance(benchmark, dict) or any(not isinstance(benchmark.get(k), str)
                for k in ('videoKey', 'posterKey', 'videoMediaId', 'posterMediaId')):
            raise WebContentError('benchmark 必须是对象并提供准确 videoKey/posterKey/videoMediaId/posterMediaId')
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
        if business['schema_version'] != '4.0':
            raise WebContentError('当前导演适配只支持已核 v4 JSON，旧格式不猜拍法')
        for scene in business['scenes']:
            lines = [scene['audiovisual_flow']]
            for shot in scene['shots']:
                # Original IDs stay unchanged. Case-specific DR05 card aliases are
                # deliberately not fabricated; the fixed renderer retains generic text.
                lines += ['\n镜头：' + shot['shot_id'], '观看目的：' + shot['purpose'],
                          '机位：' + canonical(shot['camera']),
                          '起态：' + shot['take']['start'], '镜内过程：' + shot['take']['process'],
                          '末态：' + shot['take']['end'], '节奏：' + shot['rhythm'],
                          '对白与声音：' + canonical(shot['audio']), '切出：' + canonical(shot['cut'])]
            block(scene['scene_id'], scene['scene_id'] + ' 导演视听展开', '\n\n'.join(lines))
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
    elif stage == 'prompts':
        if not isinstance(business.get('drafts'), list) or not business['drafts']:
            raise WebContentError('缺少现行完整视频 drafts JSON；不恢复退役 production_plan')
        for draft in business['drafts']:
            if not isinstance(draft, dict):
                raise WebContentError('drafts 每项必须是视频段对象')
            ordered, checks = draft.get('ordered_media'), draft.get('reference_checks')
            if not isinstance(ordered, list) or not isinstance(checks, list) or len(ordered) != len(checks):
                raise WebContentError('视频素材职责不能遗漏实际输入')
            if any(not isinstance(item, dict) or any(not isinstance(item.get(k), str) or not item[k].strip()
                   for k in ('node_id','media_id')) for item in ordered):
                raise WebContentError('视频实际输入缺少准确 native node_id/media_id')
            if any(not isinstance(ref, dict) or any(not isinstance(ref.get(k), str)
                    for k in ('artifact_id', 'revision', 'sha256')) for ref in checks):
                raise WebContentError('reference_checks 每项必须提供准确 artifact_id/revision/sha256')
            refs = [media(ref['artifact_id'] + '@' + ref['revision'],
                          reference_anchor(draft['node_id'], {'reference':ref,'native':native}), ref['sha256'])
                    for native, ref in zip(ordered, checks)]
            block(draft['node_id'], '视频段：' + draft['node_id'], draft['prompt'] + '\n\n参数：' + canonical(draft['parameters']), refs)
    elif stage in {'white_model','storyboard'}:
        if not business.get('files'):
            raise WebContentError('缺少已登记预览 files；不能从 assets-v2 缺失路径补造媒体')
        if not isinstance(business['files'], list) or any(not isinstance(f, dict)
                or not isinstance(f.get('path'), str) for f in business['files']):
            raise WebContentError('files 必须是提供准确 path 的预览对象数组')
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
            expected_version=job['expectedVersion'], scope=job['scope'], media_registry=job['mediaRegistry'],
            benchmark=job.get('benchmark'), title=job.get('title'), reviewable=job.get('reviewable', False),
            contract_root=job.get('contractRoot', CONTRACT), project_root=job.get('projectRoot'))
        print(canonical(result))
        return 0
    except (ValueError, KeyError, TypeError, OSError, subprocess.SubprocessError) as exc:
        print(canonical({'ok':False,'published':False,'error':str(exc)}), file=sys.stderr)
        return 2


if __name__ == '__main__':
    raise SystemExit(main())
