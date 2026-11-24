import pytest

from scripts.core.artifacts import digest
from scripts.core import redframe_recreation
from scripts.core.depth_reference_review import DEPTH_REFERENCE_EXCLUSION, check_depth_group, product_visible_frames
from scripts.core.redframe_recreation import _require_appearance_outputs, _serialize, _validate_group_contents


def test_recreation_group_keeps_both_motion_inputs_without_director(tmp_path):
    plan = {'fps': 30, 'segments': [
        {'id': 'SEG-01', 'frames': [0, 90], 'motion': '走位', 'appearance': ['女主'],
         'dialogue_ids': ['DLG-01']},
        {'id': 'SEG-02', 'frames': [90, 180], 'motion': '刷涂', 'appearance': ['女主'],
         'dialogue_ids': ['DLG-02']},
    ], 'references': {}}
    keys = ['走位', '女主', '刷涂']
    for index, (key, role) in enumerate(zip(keys, ['depth', 'appearance', 'product'], strict=True)):
        path = tmp_path / f'{index}.png'
        path.write_bytes(key.encode())
        plan['references'][key] = {'path': path.name, 'sha256': digest(path), 'role': role}
    group = {'id': 'SEG-01+02', 'source_segment_ids': ['SEG-01', 'SEG-02'], 'node_name': '草稿一',
             'source_frames': [0, 180], 'generate_duration_seconds': 6,
             'reference_keys': keys, 'dialogue_ids': ['DLG-01', 'DLG-02'],
             'materials': [{'reference': '@' + key, **plan['references'][key]} for key in keys],
             'prompt': '@走位 @女主 说“你好。”\n随后按 @刷涂 操作，说“马上。”'}
    packet = {'fps': 30, 'groups': [group]}
    narrative = {'dialogues': [{'dialogue_id': 'DLG-01', 'text': '你好。'},
                               {'dialogue_id': 'DLG-02', 'text': '马上。'}]}

    _validate_group_contents(tmp_path, packet, plan, narrative)
    group['materials'][2]['sha256'] = plan['references']['走位']['sha256']
    with pytest.raises(ValueError, match='与原复刻计划不符'):
        _validate_group_contents(tmp_path, packet, plan, narrative)
    group['materials'][2]['sha256'] = plan['references']['刷涂']['sha256']
    group['prompt'] = group['prompt'].replace('马上。', '等一下。')
    with pytest.raises(ValueError, match='DLG-02'):
        _validate_group_contents(tmp_path, packet, plan, narrative)


def test_serialization_binds_native_identity_instead_of_original_slot_order():
    group = {'reference_keys': ['同窗深度', '人脸', '实操'],
             'prompt': '用 @同窗深度 锁走位和 @人脸 。\n切到 @实操 ，继续 @人脸 。'}
    ordered = [('draft', 'depth'), ('face', 'image'), ('draft', 'product')]
    tokens = {ident: (f'{{{{ref {mark}}}}}', {'type': 'Custom', 'html': str(ident)})
              for ident, mark in zip(ordered, [2, 1, 3], strict=True)}
    body, rich = _serialize(group, ordered, tokens)
    assert body == '用 {{ref 2}} 锁走位和 {{ref 1}} 。\n切到 {{ref 3}} ，继续 {{ref 1}} 。'
    assert [token['html'] for row in rich for token in row if token['type'] == 'Custom'] == [
        str(ordered[0]), str(ordered[1]), str(ordered[2]), str(ordered[1])]


def test_appearance_rejects_raw_face_next_to_a_formal_board(tmp_path, monkeypatch):
    board = {'path': 'board.png', 'sha256': 'board', 'asset_id': 'AST-LEAD', 'output_id': 'OUTFIT'}
    plan = {'segments': [{'appearance': ['造型', '原照', '礼盒']}],
            'references': {'造型': board, '原照': {'path': 'face.jpg', 'sha256': 'face'},
                           '礼盒': {'path': 'box.png', 'sha256': 'box'}}}
    monkeypatch.setattr(redframe_recreation, 'resolve_product_pack',
                        lambda _: {'references': [{'role': 'packaging', 'sha256': 'box'}]})
    product = {'mode': 'replace'}
    with pytest.raises(ValueError, match='缺少正式资产输出绑定：原照'):
        _require_appearance_outputs(tmp_path, plan, product)

    plan['segments'][0]['appearance'].remove('原照')

    def resolve(project, bindings):
        assert project == tmp_path
        assert bindings == ['AST-LEAD@OUTFIT']
        return {'AST-LEAD@OUTFIT': {'path': tmp_path / 'board.png', 'sha256': 'board',
                                    'requires_face_certification': True}}

    monkeypatch.setattr(redframe_recreation, 'resolve_final_images', resolve)
    assert _require_appearance_outputs(tmp_path, plan, product) == {'造型'}
    board['path'] = 'unapproved-copy.png'
    with pytest.raises(ValueError, match='文件或字节不符'):
        _require_appearance_outputs(tmp_path, plan, product)


def test_lossless_product_rendition_preserves_the_pinned_image(tmp_path, monkeypatch):
    from PIL import Image

    original = tmp_path / 'film.png'
    rendition = tmp_path / 'film.webp'
    image = Image.new('RGB', (12, 18), 'brown')
    image.save(original)
    image.save(rendition, lossless=True)
    locked = digest(original)
    monkeypatch.setattr(redframe_recreation, 'resolve_product_pack',
                        lambda _: {'references': [{'role': 'state', 'sha256': locked, 'path': original}]})
    reference = {'path': rendition.name, 'sha256': digest(rendition), 'lossless_product_source_sha256': locked}
    plan = {'segments': [{'appearance': ['膜态']}], 'references': {'膜态': reference}}
    _require_appearance_outputs(tmp_path, plan, {'mode': 'replace'})

    image.putpixel((2, 3), (0, 0, 0))
    image.save(rendition, lossless=True)
    reference['sha256'] = digest(rendition)
    with pytest.raises(ValueError, match='逐像素一致'):
        _require_appearance_outputs(tmp_path, plan, {'mode': 'replace'})


def test_product_png_rendition_is_only_a_proportional_resize(tmp_path, monkeypatch):
    from PIL import Image

    original = tmp_path / 'film.png'
    rendition = tmp_path / 'smaller.png'
    image = Image.new('RGBA', (12, 20), 'teal')
    image.putpixel((4, 7), (239, 120, 14, 255))
    image.save(original)
    locked = digest(original)
    monkeypatch.setattr(redframe_recreation, 'resolve_product_pack',
                        lambda _: {'references': [{'role': 'state', 'sha256': locked, 'path': original}]})
    reference = {'path': rendition.name, 'resized_product_source_sha256': locked}
    plan = {'segments': [{'appearance': ['膜态']}], 'references': {'膜态': reference}}
    image.resize((6, 10), Image.Resampling.LANCZOS).save(rendition)
    reference['sha256'] = digest(rendition)
    _require_appearance_outputs(tmp_path, plan, {'mode': 'replace'})

    image.resize((6, 11), Image.Resampling.LANCZOS).save(rendition)
    reference['sha256'] = digest(rendition)
    with pytest.raises(ValueError, match='等比缩小'):
        _require_appearance_outputs(tmp_path, plan, {'mode': 'replace'})


def test_depth_delivery_checks_source_window_and_prompt_without_output_review():
    ref = {'role': 'depth', 'source_start_frame': 0, 'frame_count': 90}
    plan = {'first_frame': 0, 'end_frame': 180, 'product_visible_frames': [[60, 90]],
            'references': {'深度': ref}}
    group = {'id': 'SEG-01', 'reference_keys': ['深度'], 'prompt': '@深度 不生成字幕。'}
    windows = product_visible_frames(plan, required=True)
    with pytest.raises(ValueError, match='覆盖产品可见帧'):
        check_depth_group(plan, group, windows)

    ref['frame_count'] = 60
    with pytest.raises(ValueError, match='明确忽略其中的字幕'):
        check_depth_group(plan, group, windows)
    group['prompt'] += DEPTH_REFERENCE_EXCLUSION
    check_depth_group(plan, group, windows)
    ref['subtitle_check'] = 'remaining'
    check_depth_group(plan, group, windows)


def test_new_depth_delivery_requires_reviewed_product_windows():
    plan = {'first_frame': 0, 'end_frame': 180}
    assert product_visible_frames(plan) is None
    with pytest.raises(ValueError, match='登记产品可见帧窗'):
        product_visible_frames(plan, required=True)
    plan['product_visible_frames'] = []
    assert product_visible_frames(plan, required=True) == []


def test_product_group_must_keep_normal_shots_before_and_after_product(tmp_path, monkeypatch):
    from scripts.core.io import write_json
    from scripts.core.recreation_segments import PLAN, PROMPTS

    write_json(tmp_path / 'narrative.json', {})
    checksum = digest(tmp_path / 'narrative.json')
    canvas = {'platform': 'redframe'}
    entry = {'artifact_id': 'narrative', 'logical_id': 'narrative.package', 'revision': 'R02',
             'checksum': checksum, 'path_or_url': 'narrative.json'}
    monkeypatch.setattr(redframe_recreation, 'load_kickoff', lambda _: {
        'creation': {'workflow': 'depth-guided-recreation'}, 'canvas': canvas})
    monkeypatch.setattr(redframe_recreation, 'load_project_state', lambda _: {'current_artifacts': ['narrative']})
    monkeypatch.setattr(redframe_recreation, 'load_manifest', lambda _: {'artifacts': [entry]})
    monkeypatch.setattr(redframe_recreation, 'artifact_is_adopted', lambda *_: True)
    monkeypatch.setattr(redframe_recreation, 'segment_steps', lambda *_, **__: [])
    monkeypatch.setattr(redframe_recreation, '_validate_group_contents', lambda *_: None)
    monkeypatch.setattr(redframe_recreation, '_require_appearance_outputs', lambda *_: None)

    group_path = tmp_path / 'groups.json'
    plan = {'first_frame': 0, 'end_frame': 180, 'segments': [
        {'id': '玄关', 'mode': 'product-display', 'frames': [0, 180]},
    ]}

    def preflight():
        write_json(tmp_path / PLAN, plan)
        plan_hash = digest(tmp_path / PLAN)
        write_json(tmp_path / PROMPTS, {'narrative_id': 'narrative', 'narrative_sha256': checksum,
                                        'plan_sha256': plan_hash})
        write_json(group_path, {'canvas': canvas, 'narrative_id': 'narrative', 'narrative_revision': 'R02',
                                'narrative_sha256': checksum, 'plan_path': str(PLAN), 'plan_sha256': plan_hash,
                                'source_prompt_path': str(PROMPTS), 'source_prompt_sha256': digest(tmp_path / PROMPTS),
                                'groups': []})
        return redframe_recreation.recreation_groups(tmp_path, group_path)

    with pytest.raises(ValueError, match='登记产品可见帧窗'):
        preflight()
    plan['product_visible_frames'] = [[100, 120], [60, 100]]
    with pytest.raises(ValueError, match='第 0 帧.*保留同窗深度'):
        preflight()
    plan['segments'] = [
        {'id': '产品前', 'mode': 'depth', 'frames': [0, 60]},
        {'id': '玄关', 'mode': 'product-display', 'frames': [60, 180]},
    ]
    with pytest.raises(ValueError, match='第 120 帧.*保留同窗深度'):
        preflight()
    plan['segments'][1]['frames'][1] = 120
    plan['segments'].append({'id': '产品后', 'mode': 'text', 'frames': [120, 180]})
    with pytest.raises(ValueError, match='产品后.*缺少可用深度的具体原因'):
        preflight()
    plan['segments'][2]['depth_omission_reason'] = '母片该镜已有大幅烧录文字，现有深度无法清理'
    preflight()
    plan['segments'][2] = {'id': '产品后', 'mode': 'depth', 'frames': [120, 180]}
    preflight()


def test_changed_groups_use_saved_content_even_without_manual_flags(tmp_path):
    from copy import deepcopy
    from scripts.core.io import write_json
    from scripts.core.redframe_recreation import _revision_scope

    groups = [{'id': 'A', 'prompt': '旧姿势', 'materials': []},
              {'id': 'B', 'previous_id': 'older-B', 'prompt': '继续对白', 'materials': []}]
    path = tmp_path / 'previous.json'
    write_json(path, {'groups': groups})
    previous = {'phase': 'after_refresh', 'group_path': path.name, 'group_sha256': digest(path),
                'nodes': {'A': 'n1', 'B': 'n2'},
                'rows': [{'source_segment_id': k, 'node_id': ident} for k, ident in [('A', 'n1'), ('B', 'n2')]]}
    packet = {'groups': deepcopy(groups)}
    assert _revision_scope(tmp_path, packet, previous)[0] == []
    packet['groups'][0]['prompt'] = '双管宽面贴桌'
    changed, unchanged, _ = _revision_scope(tmp_path, packet, previous)
    assert changed == [packet['groups'][0]]
    assert unchanged['B']['node_id'] == 'n2'
    packet['groups'][0]['id'] = 'new-A'
    with pytest.raises(ValueError, match='previous_id'):
        _revision_scope(tmp_path, packet, previous)
    packet['groups'][0]['previous_id'] = 'A'
    assert _revision_scope(tmp_path, packet, previous)[0] == [packet['groups'][0]]
    previous['rows'][0]['node_id'] = 'wrong'
    with pytest.raises(ValueError, match='节点回读'):
        _revision_scope(tmp_path, packet, previous)


def test_supplemental_product_motion_matches_prior_native_bytes_and_segment(tmp_path):
    from scripts.core.io import write_json
    from scripts.core.redframe_recreation import _with_delivered_supplements

    path = tmp_path / 'groups.json'
    packet = {'narrative_sha256': 'n', 'supplemental_product_motion': {'s': ['wipe']},
              'groups': [{'id': 'g', 'source_segment_ids': ['s'], 'reference_keys': ['wipe'],
                          'materials': [{'reference': '@wipe', 'sha256': 'hash', 'role': 'product',
                                         'uses_by_source_segment': ['s']}]}]}
    write_json(path, packet)
    saved = {'group_path': path.name, 'group_sha256': digest(path), 'phase': 'after_refresh',
             'nodes': {'g': 'draft'}, 'rows': [{'source_segment_id': 'g', 'node_id': 'draft',
                                                 'ordered_media': [{'sha256': 'hash'}]}]}
    plan = {'segments': [{'id': 's', 'mode': 'product'}],
            'references': {'wipe': {'role': 'product', 'sha256': 'hash'}}}
    product = {'product_usage': 'real_use'}
    assert _with_delivered_supplements(tmp_path, packet, plan, saved, product) == plan
    plan['references']['wipe']['sha256'] = 'new'
    with pytest.raises(ValueError, match='同款实操'):
        _with_delivered_supplements(tmp_path, packet, plan, saved, product)


def test_creation_marker_without_visible_output_never_creates_again():
    from types import SimpleNamespace
    from scripts.core.redframe_recreation import _reuse_or_create

    ui = SimpleNamespace(workspace=SimpleNamespace(read_canvas=lambda: {'nodes': []}))
    state = {'assets': {}, 'creating': dict(field='assets', key='k', name='素材', before_ids=[])}
    with pytest.raises(ValueError, match='结果未知'):
        _reuse_or_create(ui, state, 'assets', 'k', '素材', lambda *a: pytest.fail('duplicate creation'),
                         lambda: pytest.fail('overwrote original marker'))


def test_completed_recreation_retry_returns_before_opening_canvas(tmp_path, monkeypatch):
    from scripts import redframe_draft
    from scripts.core.io import write_json

    group = {'id': 'SEG-04'}
    packet = {'groups': [group]}
    canvas = {'target_url': 'canvas', 'target_project_id': 'project'}
    source = tmp_path / 'groups.json'
    write_json(source, packet)
    monkeypatch.setattr(redframe_recreation, 'recreation_groups', lambda *args, **kwargs: (packet, canvas))

    def unexpected_canvas(*args, **kwargs):
        pytest.fail('a completed retry should never open the canvas')

    monkeypatch.setattr(redframe_draft, 'open_workspace', unexpected_canvas)
    path = tmp_path / redframe_recreation.DELIVERY_RECEIPT
    write_json(path, {'recreation': dict(
        phase='after_refresh', group_path=source.name, group_sha256=digest(source),
    )})
    before = path.read_bytes()
    result = redframe_recreation.write_recreation(tmp_path, source, host_browser=None,
                                                  append_revision=True, changed_only=True)
    assert result['written_draft_count'] == 0
    assert path.read_bytes() == before


def test_reference_mode_accepts_native_reordering_without_changing_capabilities():
    import json

    canvas = dict(model='Seedance 2.5', aspect_ratio='9:16', resolution='480p')
    data = dict(model=json.dumps(redframe_recreation._SEEDANCE_MODEL), videoMode='reference',
                mode=json.dumps(['videoReference:10', 'imageReference:30', 'audioReference:10']),
                ratio='9:16', resolution='480p', duration=7)
    redframe_recreation._verify_parameters(data, 7, canvas, populated=True)
    data['mode'] = json.dumps(['videoReference:10', 'imageReference:30', 'audioReference:11'])
    with pytest.raises(ValueError, match='参考模式'):
        redframe_recreation._verify_parameters(data, 7, canvas, populated=True)
