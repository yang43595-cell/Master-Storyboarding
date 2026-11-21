"""Offline action-protocol tests, not a live Redframe integration/visual test.

Native field/chip shapes are sanitized from the airport run's persisted node
readback (2026-11-17 17:06 UTC). Page objects below are explicit test doubles;
they do not claim that current production DOM or provider submission was tested.
"""
from contextlib import contextmanager
from copy import deepcopy
import json
from pathlib import Path
from urllib.parse import quote

import pytest

from scripts.core.redframe_image_ui import (
    ImageUIError, RedframeImageUI, _prompt_parts, image_browser_session,
)
from scripts.core.redframe_workspace import RedframeWorkspaceError

URL = 'https://redframe-ai.xmpaohong.com:28888/#/workspace?canvasId=canvas'
MODEL = '["volcengineArkVideo","doubao-seedream-5-5-261028"]'
SETTINGS = dict(model=MODEL, ratio='9:16', size='1K', count=1)
SOURCE_HASH = 'a' * 64


def graph():
    # The Write/Custom nesting, imageReference class, percent-encoded identity,
    # data-value and model representation are the observed native format.
    chip = ('<span style="display: inline-block;"><span class="imageReference" '
            f'data-reference-id="{quote(json.dumps(["source", "image"]), safe="")}" '
            'data-value="{{ref 1}}">参考 1</span></span>')
    data = {**SETTINGS, 'label': 'image', 'prompt': '{{ref 1}} reference description',
            'promptModel': [[{'type': 'Write', 'text': ''}, {'type': 'Custom', 'html': chip},
                             {'type': 'Write', 'text': ' reference description'}]],
            'outputs': {'image': {'value': {'url': 'assets/target/old.png'}}}}
    return {'toonflowCanvas': True, 'nodes': [
        {'id': 'source', 'type': 'remote-imageNode', 'data': {
            'outputs': {'image': {'value': {'url': 'assets/source/original.png'}}}}},
        {'id': 'target', 'type': 'remote-imageGenerationNode', 'data': data}],
        'edges': [{'source': 'source', 'sourceHandle': 'image', 'target': 'target', 'targetHandle': 'in'}]}


class Workspace:
    read_budget = 30_000

    def __init__(self, value=None):
        self.graph = graph() if value is None else value
        self.hash = SOURCE_HASH
        self.waits = []
        self.on_digest = None

    def read_canvas(self, **_kwargs):
        return deepcopy(self.graph)

    def digest_image_outputs(self, nodes):
        if self.on_digest:
            self.on_digest()
        return [{'node_id': n['id'], 'status': 200, 'error': None, 'sha256': self.hash} for n in nodes]

    def wait_for_image_result(self, node_id, previous_output, *, timeout):
        self.waits.append((node_id, previous_output, timeout))
        return 'assets/target/new.png'


class Control:
    def __init__(self, page, name, *, count=1):
        self.page, self.name, self.matches = page, name, count

    def count(self):
        return self.matches

    def wait_for(self, **kwargs):
        assert kwargs == dict(state='attached', timeout=7000)
        self.page.waits.append(self.name)

    def get_attribute(self, attr, **kwargs):
        assert attr == 'title'
        assert kwargs['timeout'] == 7000
        return self.page.project_title

    def bounding_box(self, **kwargs):
        assert kwargs['timeout'] == 7000
        return self.page.box

    def click(self, **kwargs):
        self.page.clicks.append((self.name, kwargs))
        assert kwargs['timeout'] == 7000
        if self.name == self.page.fail_click:
            raise TimeoutError('test click outcome intentionally unknown')
        if self.name == '适应视图' and self.page.fit_box:
            self.page.box = self.page.fit_box

    def get_by_role(self, role, *, name, exact):
        return self.page.get_by_role(role, name=name, exact=exact)

    def locator(self, selector):
        return Control(self.page, selector)


class Page:
    def __init__(self):
        self.url, self.project_title = URL, '/app/data/workspaces/project'
        self.viewport_size = {'width': 3000, 'height': 2200}
        self.box = dict(x=100, y=100, width=300, height=300)
        self.fit_box = None
        self.fail_click = None
        self.clicks, self.timeouts, self.waits, self.closed = [], [], [], False
        self.duplicate_generate = False
        self.dialog_count = 0

    def set_default_timeout(self, value):
        self.timeouts.append(value)

    def set_default_navigation_timeout(self, value):
        self.timeouts.append(value)

    def set_viewport_size(self, value):
        self.viewport_size = value

    def get_by_label(self, name, *, exact):
        assert exact and name == '项目名称'
        return Control(self, name)

    def get_by_role(self, role, *, name, exact):
        assert exact
        count = self.dialog_count if name == '关闭对话' else 2 if name == '生成图片' and self.duplicate_generate else 1
        return Control(self, name, count=count)

    def locator(self, selector):
        return Control(self, selector)

    def goto(self, url, **kwargs):
        assert kwargs == dict(wait_until='domcontentloaded', timeout=30000)
        self.url = url

    def close(self):
        self.closed = True


def adapter():
    page, workspace = Page(), Workspace()
    return RedframeImageUI(page, 'project', URL, workspace=workspace), page, workspace


def kwargs():
    return dict(settings=SETTINGS, sources={'source': SOURCE_HASH}, prompt='{{ref 1}} reference description')


def test_preflight_reads_native_chip_edge_settings_and_original_bytes():
    ui, page, _ = adapter()
    result = ui.preflight('target', **kwargs())
    assert result['previous_output'] == 'assets/target/old.png'
    assert result['canvas_id'] == 'canvas' and result['project_id'] == 'project'
    assert len(result['input_sha256']) == 64
    assert page.clicks == []


@pytest.mark.parametrize('fault', ['wrong_source_bytes', 'wrong_ratio', 'wrong_port', 'extra_edge',
                                  'fake_plain_reference', 'unknown_chip', 'changed_while_reading'])
def test_preflight_rejects_real_input_conflicts_without_clicking(fault):
    ui, page, workspace = adapter()
    data = workspace.graph['nodes'][1]['data']
    if fault == 'wrong_source_bytes':
        workspace.hash = 'b' * 64
    elif fault == 'wrong_ratio':
        data['ratio'] = 'adaptive'
    elif fault == 'wrong_port':
        workspace.graph['edges'][0]['sourceHandle'] = 'text'
    elif fault == 'extra_edge':
        workspace.graph['edges'].append(dict(workspace.graph['edges'][0]))
    elif fault == 'fake_plain_reference':
        data['promptModel'] = [[dict(type='Write', text=data['prompt'])]]
    elif fault == 'unknown_chip':
        data['promptModel'][0][1]['html'] = '<span>reference</span>'
    else:
        workspace.on_digest = lambda: data.update(ratio='16:9')
    with pytest.raises((ImageUIError, ValueError)):
        ui.preflight('target', **kwargs())
    assert page.clicks == []


def test_recorded_native_prompt_format_preserves_text_and_identity():
    data = graph()['nodes'][1]['data']
    text, mentions = _prompt_parts(data)
    assert (text, mentions) == ('{{ref 1}} reference description', [('source', '{{ref 1}}')])
    data['promptModel'][0].append(dict(type='Write', text='\n\ufeff '))
    assert _prompt_parts(data)[0].endswith('\n\ufeff ')


def test_preflight_rejects_swapped_native_ref_numbers_even_with_same_source_set():
    ui, page, workspace = adapter()
    workspace.graph['nodes'].insert(1, {'id': 'second', 'type': 'remote-imageNode', 'data': {
        'outputs': {'image': {'value': {'url': 'assets/second/other.png'}}}}})
    workspace.graph['edges'].append(dict(source='second', sourceHandle='image', target='target', targetHandle='in'))
    data = workspace.graph['nodes'][-1]['data']
    first_chip = data['promptModel'][0][1]['html']
    wrong_first = first_chip.replace(quote(json.dumps(['source', 'image']), safe=''), quote(json.dumps(['second', 'image']), safe=''))
    wrong_second = first_chip.replace('{{ref 1}}', '{{ref 2}}')
    data['promptModel'] = [[dict(type='Custom', html=wrong_first), dict(type='Custom', html=wrong_second)]]
    data['prompt'] = '{{ref 1}}{{ref 2}}'
    with pytest.raises(ImageUIError, match='slot order'):
        ui.preflight('target', settings=SETTINGS, sources={'source': SOURCE_HASH, 'second': SOURCE_HASH}, prompt=data['prompt'])
    assert page.clicks == []


def test_project_identity_waits_for_the_spa_control_before_inspection():
    ui, page, _ = adapter()
    ui.assert_target()
    assert page.waits == ['项目名称']


def test_timeout_after_generate_never_replays_even_in_a_new_adapter():
    ui, page, workspace = adapter()
    page.fail_click = '生成图片'
    receipt, saved = {}, []
    with pytest.raises(ImageUIError) as error:
        ui.submit_once('target', **kwargs(), receipt=receipt, save_receipt=lambda r: saved.append(deepcopy(r)))
    assert error.value.mutation_unknown
    assert saved[0]['image_submission']['status'] == 'unknown'
    # Reading the original receipt in a new process/session still blocks replay.
    recovered = RedframeImageUI(page, 'project', URL, workspace=workspace)
    with pytest.raises(ImageUIError, match='already recorded'):
        recovered.submit_once('target', **kwargs(), receipt=deepcopy(saved[0]), save_receipt=lambda _: None)
    assert sum(name == '生成图片' for name, _ in page.clicks) == 1
    result = recovered.query(saved[0], timeout=12)
    assert result['status'] == 'output_observed' and not result['visual_quality_verified']
    assert workspace.waits == [('target', 'assets/target/old.png', 12)]


def test_failed_receipt_save_prevents_provider_click():
    ui, page, _ = adapter()
    def fail_save(_receipt):
        raise OSError('read-only test destination')
    with pytest.raises(OSError):
        ui.submit_once('target', **kwargs(), receipt={}, save_receipt=fail_save)
    assert not any(name == '生成图片' for name, _ in page.clicks)


def test_duplicate_generate_control_is_not_resolved_with_first_or_last():
    ui, page, _ = adapter()
    page.duplicate_generate = True
    with pytest.raises(ImageUIError):
        ui.submit_once('target', **kwargs(), receipt={}, save_receipt=lambda _: None)
    assert not any(name == '生成图片' for name, _ in page.clicks)


@pytest.mark.parametrize('fault', ['canvas', 'project'])
def test_target_guard_blocks_other_canvas_and_project(fault):
    ui, page, _ = adapter()
    if fault == 'canvas':
        page.url = URL.replace('canvasId=canvas', 'canvasId=other')
    else:
        page.project_title = '/app/data/workspaces/other'
    with pytest.raises(ImageUIError):
        ui.preflight('target', **kwargs())
    assert page.clicks == []


def test_focus_fits_once_then_returns_actionable_failure():
    ui, page, _ = adapter()
    page.box = dict(x=-100, y=100, width=300, height=300)
    with pytest.raises(ImageUIError, match='outside the task viewport'):
        ui.focus('target')
    assert [name for name, _ in page.clicks] == ['适应视图']


def test_already_saved_edge_does_not_draw_again():
    ui, page, _ = adapter()
    ui.connect('source', 'target')
    assert page.clicks == []


def test_saved_state_poll_retries_transient_read_without_repeating_ui_action(monkeypatch):
    ui, page, workspace = adapter()
    calls = []

    def read(**kwargs):
        calls.append(kwargs['budget_ms'])
        if len(calls) == 1:
            raise RedframeWorkspaceError('canvas read', [{'status': None, 'error': 'budget'}])
        return graph()

    monkeypatch.setattr(workspace, 'read_canvas', read)
    assert ui._wait_canvas(lambda snapshot: snapshot['edges'], 'wire reference') == graph()['edges']
    assert len(calls) == 2
    assert page.clicks == []


def test_unknown_dialogue_selector_is_not_blindly_dismissed():
    ui, page, _ = adapter()
    page.dialog_count = 2
    with pytest.raises(ImageUIError, match='exactly one'):
        ui.close_dialog()
    assert page.clicks == []


def test_browser_scope_closes_page_and_owned_context_on_interrupt():
    page, events = Page(), []
    @contextmanager
    def task_browser(**options):
        assert options == {'redframe': True}
        class Context:
            def new_page(self):
                return page
        try:
            yield Context()
        finally:
            events.append('context_closed')
    with pytest.raises(KeyboardInterrupt):
        with image_browser_session(task_browser, project_id='project', canvas_url=URL):
            raise KeyboardInterrupt()
    assert page.closed and events == ['context_closed']


def test_query_rejects_other_canvas_receipt():
    ui, _, workspace = adapter()
    receipt = {'image_submission': {'node_id': 'target', 'canvas_id': 'other', 'project_id': 'project'}}
    with pytest.raises(ImageUIError, match='another project/canvas'):
        ui.query(receipt)
    assert workspace.waits == []


def test_local_editor_keeps_multiline_text_and_recovers_focus_after_menu_selection():
    """Local contenteditable behavior only; this HTML is NOT captured Redframe DOM."""
    playwright = pytest.importorskip('playwright.sync_api')
    edge = Path('/Applications/Microsoft Edge.app/Contents/MacOS/Microsoft Edge')
    if not edge.is_file():
        pytest.skip('local headless Edge required for the isolated editor behavior test')
    html = '''<div id="node"><div contenteditable="true" aria-label="生成提示词"></div>
      <button id="refs" aria-label="引用参考素材">引用参考素材</button></div>
      <button id="item" style="display:none">图片1</button><input id="other">
      <script>
      const ed = document.querySelector('[contenteditable]');
      document.querySelector('#refs').onclick = () => document.querySelector('#item').style.display = 'block';
      document.querySelector('#item').onclick = () => {
        const chip = document.createElement('span');
        chip.className = 'imageReference'; chip.contentEditable = 'false';
        chip.setAttribute('data-reference-id', encodeURIComponent(JSON.stringify(['source', 'image'])));
        chip.setAttribute('data-value', '{{ref 1}}'); chip.textContent = '参考 1';
        (ed.lastElementChild || ed).appendChild(chip);
        document.querySelector('#item').style.display = 'none';
        document.querySelector('#other').focus();
      };
      </script>'''
    with playwright.sync_playwright() as runtime:
        browser = runtime.chromium.launch(executable_path=str(edge), headless=True)
        try:
            page = browser.new_page()
            page.route('**/*', lambda route: route.abort())
            page.set_content(html)
            ui = RedframeImageUI(page, 'project', URL, workspace=Workspace())
            # Only exercise editor writes. No target/site identity or persistence is
            # certified by this local behavior test; those use recorded-field tests.
            ui.assert_target = lambda: None
            ui.focus = lambda _node_id: page.locator('#node')
            ui._wait_canvas = lambda _predicate, _operation: None
            prompt = ui.write_prompt('target', ['first line\nsecond line',
                {'index': 1, 'source_id': 'source', 'text': '：description'}, '\nfinal line'])
            actual = page.get_by_label('生成提示词').evaluate('''element => {
              const clone = element.cloneNode(true);
              clone.querySelectorAll('.imageReference').forEach(chip => chip.replaceWith(document.createTextNode(chip.dataset.value)));
              document.body.appendChild(clone);
              const text = clone.innerText; clone.remove(); return text;
            }''')
            assert actual == prompt == 'first line\nsecond line{{ref 1}}：description\nfinal line'
            assert page.locator('#other').input_value() == ''
            assert page.get_by_label('生成提示词').locator('.imageReference').count() == 1
        finally:
            browser.close()
