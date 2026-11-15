import json

import pytest

from scripts.core.asset_batch_receipt import summarize_asset_batch_receipt
from scripts.core.io import sha256_file


TARGET = 'AST-CHARACTER@CHARACTER-FINAL'
SECOND = 'AST-CHARACTER@CHARACTER-WARDROBE'


def _passed_row(project, target=TARGET, candidate='C01'):
    image = project / f'{candidate}.png'
    image.write_bytes(b'fixture image bytes')
    return {
        'target': target, 'candidate_id': candidate, 'path': image.name,
        'sha256': sha256_file(image), 'observer': '/root/producer',
        'observed': {'framing': '脚部完整'}, 'visual_status': 'passed',
    }


def _save(project, *rows, **metadata):
    receipt = project / 'self-check.json'
    receipt.write_text(json.dumps({'items': rows, **metadata}), encoding='utf-8')
    return receipt


def test_exact_requested_output_uses_historical_fields_without_transport_details(tmp_path):
    passed = _passed_row(tmp_path)
    passed['output_path'] = passed.pop('path')
    passed['review_actor'] = passed.pop('observer')
    passed['observations'] = passed.pop('observed')
    del passed['visual_status']
    passed.update(review_result='pass', node_id='raw-id', screenshot='raw-image', logs='raw-logs')
    receipt = _save(tmp_path, _passed_row(tmp_path, SECOND, 'C02'), passed, approval=True)

    summary = summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])

    assert summary == {
        'receipt_path': str(receipt), 'status': 'ready_for_review', 'complete': True,
        'items': [{
            'target': TARGET, 'status': 'ready_for_review', 'candidate_id': 'C01',
            'path': str(tmp_path / 'C01.png'), 'sha256': passed['sha256'],
            'observer': '/root/producer', 'visual_status': 'passed',
        }],
    }


def test_a_received_candidate_does_not_mark_a_partial_batch_complete(tmp_path):
    receipt = _save(tmp_path, _passed_row(tmp_path))

    summary = summarize_asset_batch_receipt(tmp_path, receipt, [SECOND, TARGET])

    assert summary['status'] == 'pending'
    assert summary['complete'] is False
    assert [(item['target'], item['status']) for item in summary['items']] == [
        (SECOND, 'pending'), (TARGET, 'ready_for_review'),
    ]


def test_wardrobe_check_uses_receipt_observer_and_exact_per_item_status(tmp_path):
    passed = _passed_row(tmp_path)
    failed = _passed_row(tmp_path, SECOND, 'C02')
    for row, status in ((passed, '通过，可作为待审服装候选'), (failed, '服装外观通过，三视图构图待处理')):
        row['output_path'] = row.pop('path')
        row['visual_self_check'] = {'status': status, 'observed': row.pop('observed')}
        del row['observer'], row['visual_status']
    receipt = _save(tmp_path, passed, failed, observer='/root/wardrobe', review_status='user approved')

    summary = summarize_asset_batch_receipt(tmp_path, receipt, [TARGET, SECOND])

    assert summary['status'] == 'blocked'
    assert summary['complete'] is False
    assert summary['items'][0]['observer'] == '/root/wardrobe'
    assert summary['items'][0]['status'] == 'ready_for_review'
    assert summary['items'][1] == {
        'target': SECOND, 'status': 'blocked', 'candidate_id': 'C02',
        'observer': '/root/wardrobe', 'visual_status': 'failed', 'issue': '服装外观通过，三视图构图待处理',
    }


def test_unique_checked_pass_can_replace_failed_candidate_but_cannot_choose_between_passes(tmp_path):
    old = _passed_row(tmp_path)
    new = _passed_row(tmp_path, candidate='C02')
    old.update(visual_status='failed', blocking_issue='脚底截边')
    receipt = _save(tmp_path, new, old)

    summary = summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])
    assert summary['status'] == 'ready_for_review'
    assert summary['items'][0]['candidate_id'] == 'C02'

    old['visual_status'] = 'passed'
    receipt = _save(tmp_path, new, old)
    summary = summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])
    assert summary['status'] == 'pending'
    assert 'candidate_id' not in summary['items'][0]


@pytest.mark.parametrize('incomplete', [
    {'observer': None}, {'observed': None}, {'review_result': 'failed'},
    {'review_actor': '/root/other'}, {'sha256': 'f' * 64}, {'path': 'missing.png'},
    {'path': '../outside.png'},
])
def test_missing_conflicting_or_changed_evidence_cannot_be_ready(tmp_path, incomplete):
    row = _passed_row(tmp_path)
    row.update(incomplete)
    receipt = _save(tmp_path, row)

    summary = summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])

    assert summary['status'] == 'pending'
    assert summary['complete'] is False


def test_missing_partial_and_technical_receipts_remain_pending(tmp_path):
    receipt = tmp_path / 'self-check.json'
    assert summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])['status'] == 'pending'
    receipt.write_text('{"items":', encoding='utf-8')
    assert summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])['status'] == 'pending'
    receipt.write_text(json.dumps([{'target': TARGET, 'platform_status': 'completed'}]), encoding='utf-8')
    assert summarize_asset_batch_receipt(tmp_path, receipt, [TARGET])['status'] == 'pending'


def test_worker_can_close_an_exact_target_blocked_before_an_image_exists(tmp_path):
    blocker = {'target': SECOND, 'observer': '/root/producer', 'status': 'blocked',
               'blocking_issue': '平台提交后无法确认结果'}
    receipt = _save(tmp_path, _passed_row(tmp_path), blocker)

    summary = summarize_asset_batch_receipt(tmp_path, receipt, [TARGET, SECOND])

    assert summary['status'] == 'blocked'
    assert summary['items'][0]['status'] == 'ready_for_review'
    assert summary['items'][1] == {'target': SECOND, 'observer': '/root/producer',
                                   'status': 'blocked', 'issue': '平台提交后无法确认结果'}
    blocker.pop('observer')
    receipt = _save(tmp_path, _passed_row(tmp_path), blocker)
    assert summarize_asset_batch_receipt(tmp_path, receipt, [TARGET, SECOND])['status'] == 'pending'


def test_explicit_short_blocker_is_returned_and_raw_details_stay_in_receipt(tmp_path):
    row = _passed_row(tmp_path)
    row.update(visual_status='failed', blocking_issue='脚底截边')
    summary = summarize_asset_batch_receipt(tmp_path, _save(tmp_path, row), [TARGET])
    assert summary['items'][0]['issue'] == '脚底截边'

    row.update(blocking_issue='raw screenshot and logs ' * 30)
    summary = summarize_asset_batch_receipt(tmp_path, _save(tmp_path, row), [TARGET])
    assert summary['status'] == 'blocked'
    assert summary['items'][0]['issue'] == '实看未通过，见原回执'


def test_receipt_and_image_symlinks_cannot_leave_the_project(tmp_path):
    project = tmp_path / 'project'
    project.mkdir()
    external_image = tmp_path / 'external.png'
    external_image.write_bytes(b'fixture image bytes')
    external_receipt = _save(tmp_path, _passed_row(tmp_path))
    receipt_link = project / 'link.json'
    receipt_link.symlink_to(external_receipt)
    with pytest.raises(ValueError, match='越出项目'):
        summarize_asset_batch_receipt(project, receipt_link, [TARGET])

    row = _passed_row(project)
    (project / 'image-link.png').symlink_to(external_image)
    row['path'] = 'image-link.png'
    assert summarize_asset_batch_receipt(project, _save(project, row), [TARGET])['status'] == 'pending'
