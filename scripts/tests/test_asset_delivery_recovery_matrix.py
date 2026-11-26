"""Offline business acceptance through the asset delivery and workbench entry points."""
from copy import deepcopy
import json
from pathlib import Path
import shutil
from unittest.mock import patch

from PIL import Image
import pytest

from scripts import chat_asset_review as review
from scripts.core import artifacts, asset_refs, production_handoff
from scripts.core.io import read_json, write_json
from test_chat_asset_review import decision, image_batch, round_project
from test_visual_asset_projects import fixture, pmod, update_narrative_checksum
from test_visual_readiness import write_request


A, B, C = ('AST-PROP-PAPER', 'AST-PROP-BOX', 'AST-PROP-LABEL')


@pytest.fixture
def approvals(tmp_path):
    root = tmp_path.resolve()
    story, targets = round_project(root, ['main'])
    targets += [B + '@main', C + '@main']
    path = root / story['path_or_url']
    narrative = read_json(path)
    for ast in (B, C):
        narrative['asset_requirements'].append({**narrative['asset_requirements'][0], 'asset_id': ast, 'name': ast})
    write_json(path, narrative)
    story['checksum'] = artifacts.digest(path)
    assert review.stage.main(['adopt-package', '--project', str(root), '--artifact-id', story['artifact_id'],
                              '--logical-id', 'narrative.package', '--path', story['path_or_url'],
                              '--revision', 'R01', '--approval-ref', 'synthetic:story-approval']) == 0
    write_json(root / review.stage.REQUEST_PATH, {'asset_review_targets': targets})
    batch = image_batch(root, story, targets, 'round')
    packet = read_json(batch)
    items, assets = {}, {}
    for ast, candidate, row, color in zip((A, B, C), 'ABC', packet['items'], ('red', 'blue', 'green')):
        name = candidate + '.png'
        Image.new('RGB', (7, 5), color).save(root / name)
        sha = artifacts.digest(root / name)
        row.update(candidate_id=candidate, path=name, sha256=sha)
        confirmation = dict(id='approved-A', image_sha256=sha)
        if ast == C:
            confirmation.update(candidate_id=candidate, source_candidate=name, candidate_revision='R01')
        items[ast] = dict(ast_id=ast, output_id='main', library_asset_id=candidate, confirmation=confirmation)
        selected = dict(candidate_id=f'export-{candidate}', path=name, user_confirmation_ref='receipts.json#approved-A')
        assets[ast] = dict(asset_id=ast, status='confirmed', confirmed_asset=selected, candidates=[dict(selected)])
        if ast != A:
            assets[ast]['confirmed_outputs'] = [{**selected, 'output_id': 'main'}]
    assert len({r['sha256'] for r in packet['items']}) == len({r['candidate_id'] for r in packet['items']}) == 3
    write_json(batch, packet)
    shown = review.show(root, 'round', story)['shown']
    manifest = root / asset_refs.MASTER_DIR / 'old' / 'visual-asset-manifest.json'
    receipt = root / 'receipts.json'

    def save():
        write_json(manifest, {'assets': list(assets.values())})
        write_json(receipt, {'items': list(items.values())})

    def send(*scope):
        message = dict(scope='selected', targets=[ast + '@main' for ast in scope], at='2026-11-26T12:00:00Z',
                       quote='退回本次选中的候选', review_ref='synthetic:return', shown=[shown])
        result = review.return_for_revision(root, [shown], message, story)
        return message, result, root / result['record']

    return root, story, assets, items, save, send


def formal(root, ast, *, valid, candidate=None, slot=None):
    ref = ast + '@' + (slot or ('legacy-main' if ast == A else 'main'))
    outputs = asset_refs.confirmed_images(root).get(ast, {}).get('outputs', [])
    image = asset_refs.confirmed_images(root, requests=[ref]).get(ast, {})
    scoped = image.get('outputs', [])
    if valid:
        output, = scoped
        assert output in outputs and output['path_exists']
        assert asset_refs.resolve_final_images(root, [ref])[ref] == {**output, 'manifest': image['manifest']}
        if candidate is not None:
            assert output['source_candidate_id'] == candidate
        return output
    assert not scoped and all(row['output_id'] != ref.split('@')[1] for row in outputs)
    with pytest.raises(ValueError, match=ast):
        asset_refs.resolve_final_images(root, [ref])


@pytest.mark.parametrize('number', range(1, 14), ids=[
    '01_legacy_confirmation', '02_outer_library_id', '03_mixed_batch', '04_shared_receipt',
    '05_equivalent_paths', '06_same_file_distinct_fragment', '07_export_id_collision',
    '08_other_revision', '09_empty_old_withdrawals', '10_idempotent_withdrawal',
    '11_approve_B_replay_A', '12_incomplete_identity', '13_handoff_consistency',
])
def test_asset_withdrawal_matrix(number, approvals):
    root, story, assets, items, save, send = approvals
    if number == 7:
        for output in [assets[B]['confirmed_asset'], *assets[B]['confirmed_outputs'], *assets[B]['candidates']]:
            output['candidate_id'] = 'A'
    if number == 8:
        items[B]['confirmation']['candidate_revision'] = 'R02'
    if number == 12:
        del items[B]['library_asset_id']
        assets[B]['confirmed_outputs'].append({**assets[C]['confirmed_outputs'][0], 'output_id': 'alternate',
                                                'user_confirmation_ref': 'receipts.json#approved-alternate'})
        items['alternate'] = {**items[C], 'ast_id': B, 'output_id': 'alternate',
                              'confirmation': {**items[C]['confirmation'], 'id': 'approved-alternate'}}
    save()
    assert formal(root, A, valid=True, candidate='A')['candidate_id'] == 'export-A'
    formal(root, B, valid=number != 12, candidate='B')
    formal(root, C, valid=True, candidate='C')
    if number == 13:
        write_json(root / review.stage.REQUEST_PATH, {'kickoff': {
            'decision_ref': 'synthetic no product', 'product': {'mode': 'none'},
        }})
        before = production_handoff.resolve_production_handoff(root, actions={'assets'})['steps']
        ready = next(step for step in before if step['action'] == 'assets')
        assert ready['status'] == 'complete' and set(ready['output_ids']) == {A+'@legacy-main', B+'@main', C+'@main'}
        shots = {ast: f'S0{index}-010' for index, ast in enumerate((A, B, C), 1)}
        board = dict(schema_version='4.0', project_id='MSB-TEST', scenes=[{'shots': [
            dict(shot_id=shot, source_asset_requirement_ids=[ast]) for ast, shot in shots.items()]}])
        director = dict(artifact_id='SYNTHETIC-BOARD', revision='R01', path_or_url='03_导演/board.json',
                        checksum='e'*64, user_approved=True)
        write_request(root, director, list(shots.values()), decision='no_supplement')
        assert production_handoff.require_handoff_visual_inputs(root, board, director, shots.values())['reviewed_shot_ids'] == sorted(shots.values())
    selected = (B,) if number == 2 else (A, B) if number in {3, 4, 8, 9, 12, 13} else (A,)
    message, outcome, record = send(*selected)
    bound = read_json(record)
    if number == 4:
        assert bound['revoked_confirmations'] == ['receipts.json#approved-A'] * 2
        assert set(bound['revoked_outputs']) == {A + '@main', B + '@main'}
    if number in {5, 6}:
        ref = str(root / 'unused' / '..' / 'receipts.json')
        assets[A]['confirmed_asset']['user_confirmation_ref'] = ref + '#approved-A'
        if number == 6:
            items['second receipt'] = {**items[A], 'confirmation': {**items[A]['confirmation'], 'id': 'approved-other'}}
            assets[A]['confirmed_asset']['user_confirmation_ref'] = ref + '#approved-other'
        save()
        assert review.return_for_revision(root, message['shown'], message, story) == outcome
        if number == 5:
            bound.pop('revoked_outputs')
            write_json(record, bound)
    if number == 9:
        bound.pop('revoked_outputs')
        write_json(record, {**bound, 'revoked_confirmations': []})
    if number in {10, 11}:
        if number == 11:
            formal(root, A, valid=False)
            batch = image_batch(root, story, [A + '@main'], 'replacement')
            packet = read_json(batch)
            packet['items'][0].update(candidate_id='B', path='B.png', sha256=artifacts.digest(root / 'B.png'))
            write_json(batch, packet)
            key = review.show(root, 'replacement', story)['shown']
            approved = decision(root, key, story, 'synthetic:approval-B', at='2026-11-26T13:00:00Z', quote='B 通过')
            assert approved['count'] == 1
            formal(root, A, valid=True, candidate='B', slot='main')
        raw = record.read_bytes()
        assert review.return_for_revision(root, message['shown'], message, story) == outcome
        assert record.read_bytes() == raw
    formal(root, A, valid=number in {2, 6}, candidate='A')
    formal(root, B, valid=number not in {2, 3, 4, 9, 12, 13}, candidate='B')
    formal(root, C, valid=True, candidate='C')
    if number == 11:
        formal(root, A, valid=True, candidate='B', slot='main')
    if number == 12:
        formal(root, B, valid=True, candidate='C', slot='alternate')
    if number == 13:
        after = production_handoff.resolve_production_handoff(root, actions={'assets'})['steps']
        assert not any(step['action'] in {'assets', 'asset-review'} and step['status'] == 'complete' for step in after)
        assert all(not {A+'@legacy-main', B+'@main'} & set(step.get('confirmed_output_ids', [])) for step in after)
        for ast in (A, B):
            with pytest.raises(ValueError, match=ast):
                production_handoff.require_handoff_visual_inputs(root, board, director, [shots[ast]])
        assert production_handoff.require_handoff_visual_inputs(root, board, director, [shots[C]])['reviewed_shot_ids'] == [shots[C]]
        old = {'asset_id': B, 'output_id': 'main', 'path': 'B.png', 'sha256': artifacts.digest(root / 'B.png')}
        assets[B]['confirmed_outputs'][0] = {**assets[C]['confirmed_outputs'][0],
                                             'user_confirmation_ref': 'receipts.json#replacement'}
        items['replacement'] = {**items[C], 'ast_id': B,
                                'confirmation': {**items[C]['confirmation'], 'id': 'replacement'}}
        save()
        replacement = formal(root, B, valid=True, candidate='C')
        row = {'source_shot_ids': [shots[B]], 'materials': [old]}
        with pytest.raises(ValueError, match='路径或字节不一致'):
            production_handoff.require_handoff_visual_inputs(root, board, director, [shots[B]], rows=[row])
        row['materials'] = [{**old, 'path': replacement['path'], 'sha256': replacement['sha256']}]
        assert production_handoff.require_handoff_visual_inputs(root, board, director, [shots[B]], rows=[row])['reviewed_shot_ids'] == [shots[B]]
    assert all((root / (letter + '.png')).is_file() for letter in 'ABC')


@pytest.fixture
def workbench(tmp_path, request):
    library, project, _ = fixture(tmp_path.resolve(), product=True)
    service = library.projects
    root = Path(project['path'])
    originals = {}
    for key, color in (('stir', None), ('spread', None), ('box', 'green'), ('label', 'red')):
        # The action copies preserve arbitrary readable file bytes. Use the real
        # local catalogue and files without a dependency on video encoding tools.
        ident = library.root.name + '-' + key
        path = library.vault / '资产库' / 'synthetic' / (key + ('.png' if color else '.mp4'))
        path.parent.mkdir(parents=True, exist_ok=True)
        if color:
            Image.new('RGB', (7, 5), color).save(path)
        else:
            path.write_bytes(('synthetic action ' + key).encode())
        originals[key] = path
        record = dict(id=ident, name=key, original=str(path), media_type='image' if color else 'video',
                      category='产品' if color else '视频参考', role='参考', product_reference={
                         'product_id': 'cup', 'product_name': '蓝色杯子', 'aliases': [], 'action_key': key,
                         'description': key, 'reference_role': 'packaging' if color else 'action'})
        with library.connect() as db:
            db.execute('INSERT INTO assets VALUES (?, ?, ?)', (ident, ident, json.dumps(record, ensure_ascii=False)))
    action = dict(action='product-usage', project_id=project['id'])
    prepared = service.action({**action, 'mode': 'prepare'})
    event = prepared['inputs']['story'][0]
    keys = ('box',) if request.param else ('stir', 'spread')
    matches = {'references': [dict(product_ast_id='AST-CUP', action_key=key, paragraph_ids=[event['event_id']],
                                   evidence=event['summary']) for key in keys], 'effects': []}
    applied = service.action({**action, 'mode': 'apply', 'signature': prepared['signature'], 'result': matches})
    assert applied['product_reference_check']['status'] == 'done'
    path = root / pmod.product_usage.FILE
    result = read_json(path)
    copies = {Path(row['canonical_path']).stem: Path(row['project_path'])
              for row in result['references'] + result['material_references']}
    assert set(copies) == ({'box', 'label'} if request.param else set(originals))
    original_bytes = {key: path.read_bytes() for key, path in originals.items()}
    return root, service, action, path, copies, originals, original_bytes


@pytest.mark.parametrize(('number', 'workbench'), [
    (14, False), (15, False), (16, True), (17, True), (18, False), (19, False),
    (20, False), (21, False), (22, True), (23, False), (24, False), (25, False),
], indirect=['workbench'], ids=[
    '14_complete_hit', '15_missing_list', '16_missing_box_and_list', '17_missing_original_and_list',
    '18_only_one_action_copy', '19_only_box_copy', '20_partial_failure', '21_conflicting_copy',
    '22_incomplete_authority', '23_real_upstream_change', '24_success_then_hit', '25_preserve_matching_result',
])
def test_product_cache_matrix(number, workbench, record_property):
    root, service, action, path, copies, originals, content = workbench
    saved = read_json(path)
    raw = path.read_bytes()

    def omit_materials():
        write_json(path, {key: value for key, value in saved.items() if key != 'material_references'})

    with patch.object(service.bridge, 'executable', side_effect=AssertionError('model adapter must stay idle')) as adapter, \
         patch.object(pmod.product_usage, 'match', side_effect=AssertionError('model matcher must stay idle')) as model, \
         patch.object(shutil, 'copy2', wraps=shutil.copy2) as copier:
        def run(label, expected, copied=(), missing=()):
            copier.reset_mock()
            detail = service.action({**action, 'mode': 'codex-cli'}, background=False)
            assert not detail['source_error']
            check = detail['product_reference_check']
            assert check == service.get(action['project_id'])['product_reference_check']
            assert check['status'] == expected
            actual_copies = [Path(call.args[0]).stem for call in copier.call_args_list]
            assert actual_copies == list(copied)
            assert adapter.call_count == model.call_count == 0
            missing_files = sorted(key for key, copy in copies.items() if not copy.is_file())
            assert missing_files == sorted(missing)
            downstream = asset_refs.load_asset_list(root)['product_usage']
            if expected == 'done':
                assert downstream['status'] == 'matched'
                assert all(copy.read_bytes() == content[key] for key, copy in copies.items())
                assert read_json(path) == saved and path.read_bytes() == raw
            else:
                assert downstream['status'] == 'error'
                assert not downstream['references'] and not downstream['effects']
            intact = sorted(key for key, copy in copies.items() if key not in missing_files and copy.read_bytes() == content[key])
            evidence = {'case': number, 'run': label, 'status': expected, 'adapter': adapter.call_count,
                        'match': model.call_count, 'copy_count': len(actual_copies), 'copies': actual_copies,
                        'intact': intact, 'missing': missing_files}
            record_property('result', json.dumps(evidence))
            print(json.dumps(evidence))

        if number in {15, 16, 17, 22, 25}:
            omit_materials()
        if number in {16, 17, 19, 21, 24}:
            copies['box'].unlink()
        if number == 15:
            run('omitted material list', 'done')
            write_json(path, {**saved, 'material_references': []})
            run('empty list despite required images', 'done')
            result = {'references': [], 'effects': []}
            service.action({**action, 'mode': 'apply', 'signature': saved['signature'], 'result': result})
            saved = read_json(path)
            raw = path.read_bytes()
            copies = {}
            assert saved['material_references'] == saved['material_asset_ids'] == []
            run('verified no required materials', 'done')
            omit_materials()
            run('omitted list with no required materials', 'done')
        elif number == 17:
            originals['box'].unlink()
            run('source missing', 'error', missing=('box',))
            originals['box'].write_bytes(content['box'])
            run('retry after source restored', 'done', ('box',))
        elif number == 18:
            copies['spread'].unlink()
            run('one action copy missing', 'done', ('spread',))
        elif number == 20:
            copies['stir'].unlink()
            copies['label'].unlink()
            originals['label'].unlink()
            run('first copy repaired, later source missing', 'error', ('stir',), ('label',))
            assert copies['stir'].read_bytes() == content['stir']
            originals['label'].write_bytes(content['label'])
            run('retry after later source restored', 'done', ('label',))
        elif number == 21:
            copies['box'].write_bytes(b'conflicting project copy')
            run('different existing copy', 'error')
            assert copies['box'].read_bytes() == b'conflicting project copy'
            assert originals['box'].read_bytes() == content['box']
        elif number == 22:
            write_json(path, None)
            run('cache JSON is not an object', 'error')
            write_json(path, saved)
            run('valid cache restored', 'done')
            record = service.get(action['project_id'])
            stored = record['narrative'].pop('_product_usage')
            service.save(record)
            write_json(path, {key: value for key, value in saved.items()
                              if key not in {'material_references', 'material_asset_ids'}})
            assert pmod.product_usage.signature(record['narrative'], pmod.product_usage.project_catalogue(service.lib, record)) == saved['signature']
            run('valid input signature but old cache lacks the only material evidence', 'error')
            record = service.get(action['project_id'])
            record['narrative']['_product_usage'] = stored
            service.save(record)
            run('original applied evidence restored', 'done')
            omit_materials()
            asset = next(row for row in service.lib.items() if row['name'] == 'label')
            with service.lib.connect() as db:
                db.execute('DELETE FROM assets WHERE id=?', (asset['id'],))
            run('signed catalogue registration missing', 'error')
        elif number == 23:
            narrative_path = root / 'narrative.json'
            changed = read_json(narrative_path)
            changed['events'][0]['summary'] = '人物退到房间外。'
            write_json(narrative_path, changed)
            update_narrative_checksum(root)
            with pytest.raises(ValueError, match='上游叙事包已变化'):
                service.action({**action, 'mode': 'codex-cli'}, background=False)
            downstream = asset_refs.load_asset_list(root)['product_usage']
            assert downstream['status'] == 'stale' and not downstream['references']
            assert path.read_bytes() == raw and adapter.call_count == model.call_count == copier.call_count == 0
            evidence = dict(case=23, status='upstream rejected', adapter=0, match=0, copies=[], missing=[])
            record_property('result', json.dumps(evidence))
            print(json.dumps(evidence))
        elif number == 24:
            run('restore', 'done', ('box',))
            run('complete hit after restoration', 'done')
        elif number == 25:
            partial = deepcopy(saved)
            partial['material_references'].pop()
            partial['material_references'][0].pop('project_path')
            write_json(path, partial)
            run('reconstruct partial list without changing matched actions', 'done')
        else:
            run('cache verified', 'done', ('box',) if number in {16, 19} else ())

