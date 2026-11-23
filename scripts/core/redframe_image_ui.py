"""Bounded Redframe image actions for the task's isolated, authorized browser.

Selectors and persisted fields come from the airport image run on 2026-11-17/18:
node data-id, image/in handles, Chinese accessible labels and promptModel chips.
This is an offline implementation of that observed contract, not a live-site
certification. Check the first required image on the live site during production.
"""
from __future__ import annotations

from contextlib import contextmanager
from copy import deepcopy
from datetime import datetime, timezone
import hashlib
import json
from pathlib import Path
import re
import sys
import time
from urllib.parse import parse_qs, urlsplit

from .redframe_workspace import (NativeTag, REDFRAME_ORIGIN, RedframeWorkspace, RedframeWorkspaceError,
                                 _image_output_path, _retryable)

_ID = re.compile(r"[A-Za-z0-9_-]{1,128}\Z")
_SHA = re.compile(r"[a-f0-9]{64}\Z")
_REF = re.compile(r"\{\{ref ([1-9][0-9]*)\}\}\Z")
_IMAGE_TYPES = {'remote-imageNode', 'remote-imageGenerationNode'}


class ImageUIError(RuntimeError):
    def __init__(self, operation, detail, *, node_id=None, mutation_unknown=False):
        self.operation, self.node_id, self.mutation_unknown = operation, node_id, mutation_unknown
        super().__init__(f'{operation}: {detail}' + ('; read the original node before another write' if mutation_unknown else ''))


def _ident(value):
    if not isinstance(value, str) or not _ID.fullmatch(value):
        raise ValueError('expected an exact native node/canvas ID')
    return value


def _canvas_id(url):
    parsed = urlsplit(url)
    origin = f'{parsed.scheme}://{parsed.netloc}'
    route = urlsplit(parsed.fragment)
    values = parse_qs(route.query).get('canvasId', [])
    if origin != REDFRAME_ORIGIN or route.path != '/workspace' or len(values) != 1:
        raise ValueError('expected the exact registered Redframe workspace URL')
    return _ident(values[0])


def _one(nodes, node_id, kinds=_IMAGE_TYPES):
    matches = [node for node in nodes if node.get('id') == node_id]
    if len(matches) != 1 or matches[0].get('type') not in kinds:
        raise ImageUIError('node identity', f'missing, ambiguous or wrong type: {node_id}', node_id=node_id)
    return matches[0]


def _prompt_parts(data):
    """Recover actual text and chip identities; never manufacture a native chip."""
    rows = data.get('promptModel')
    if not isinstance(rows, list) or not rows:
        raise ImageUIError('prompt readback', 'missing native promptModel')
    texts, mentions = [], []
    for row in rows:
        if not isinstance(row, list):
            raise ImageUIError('prompt readback', 'unsupported native prompt row')
        line = []
        for part in row:
            if not isinstance(part, dict):
                raise ImageUIError('prompt readback', 'unsupported native prompt part')
            if part.get('type') == 'Write' and isinstance(part.get('text'), str):
                line.append(part['text'])
            elif part.get('type') == 'Custom' and isinstance(part.get('html'), str):
                parser = NativeTag(part['html'])
                if len(parser.refs) != 1:
                    raise ImageUIError('prompt readback', 'expected one observed image chip per Custom part')
                identity, token = parser.refs[0]
                if (not isinstance(identity, list) or len(identity) != 2 or identity[1] != 'image'
                        or not isinstance(token, str)):
                    raise ImageUIError('prompt readback', 'unsupported native image chip identity')
                mentions.append((_ident(identity[0]), token))
                line.append(token)
            else:
                raise ImageUIError('prompt readback', 'unsupported native prompt part')
        texts.append(''.join(line))
    return '\n'.join(texts), mentions


class RedframeImageUI:
    """One task page. Creation, upload, wiring and submit are never retried here."""

    def __init__(self, page, project_id, canvas_url, *, action_timeout_ms=7_000, workspace=None):
        if not isinstance(action_timeout_ms, int) or not 0 < action_timeout_ms <= 15_000:
            raise ValueError('invalid image action timeout')
        self.page, self.project_id, self.canvas_id = page, _ident(project_id), _canvas_id(canvas_url)
        self.timeout = action_timeout_ms
        self.workspace = workspace or RedframeWorkspace(page, project_id)
        self._attempted = set()
        page.set_default_timeout(action_timeout_ms)
        page.set_default_navigation_timeout(30_000)

    def assert_target(self):
        if _canvas_id(self.page.url) != self.canvas_id:
            raise ImageUIError('target', 'page left the registered canvas')
        project = self.page.get_by_label('项目名称', exact=True)
        project.wait_for(state='attached', timeout=self.timeout)
        self._unique(project, 'project identity')
        title = project.get_attribute('title', timeout=self.timeout)
        if not isinstance(title, str) or title.rstrip('/').rsplit('/', 1)[-1] != self.project_id:
            raise ImageUIError('target', 'native project title does not match registered project ID')

    @staticmethod
    def _unique(locator, operation):
        if locator.count() != 1:
            raise ImageUIError(operation, 'expected exactly one matching native control')
        return locator

    def _click(self, locator, operation, *, mutation=False, node_id=None):
        self.assert_target()
        self._unique(locator, operation)
        try:
            locator.click(timeout=self.timeout)
        except Exception as exc:
            raise ImageUIError(operation, type(exc).__name__, node_id=node_id,
                               mutation_unknown=mutation) from exc

    def close_dialog(self):
        """Only dismiss the observed close control, never arbitrary overlays/login."""
        self.assert_target()
        close = self.page.get_by_role('button', name='关闭对话', exact=True)
        if close.count() == 0:
            return
        self._click(close, 'close dialogue')

    def node(self, node_id, *, generation=False):
        node_id = _ident(node_id)
        self.assert_target()
        kinds = {'remote-imageGenerationNode'} if generation else _IMAGE_TYPES
        _one(self.workspace.read_canvas()['nodes'], node_id, kinds)
        locator = self.page.locator(f'[data-id="{node_id}"]')
        locator.wait_for(state='attached', timeout=self.timeout)
        return self._unique(locator, 'node locator')

    def focus(self, node_id):
        """One bounded fit attempt; do not loop through zoom/pan guesses."""
        self.close_dialog()
        _one(self.workspace.read_canvas()['nodes'], _ident(node_id))
        locator = self.page.locator(f'[data-id="{node_id}"]')
        box = locator.bounding_box(timeout=self.timeout) if locator.count() == 1 else None
        viewport = self.page.viewport_size
        if not self._inside(box, viewport):
            self._click(self.page.get_by_role('button', name='适应视图', exact=True), 'fit canvas')
            locator.wait_for(state='attached', timeout=self.timeout)
            self._unique(locator, 'node locator')
            box = locator.bounding_box(timeout=self.timeout)
        if not self._inside(box, viewport):
            raise ImageUIError('focus', 'node is outside the task viewport; inspect placement before continuing', node_id=node_id)
        self._click(locator.locator('.labelText'), 'focus image node', node_id=node_id)
        return locator

    @staticmethod
    def _inside(box, viewport):
        return bool(box and viewport and box['width'] > 0 and box['height'] > 0
                    and box['x'] >= 0 and box['y'] >= 0
                    and box['x'] + box['width'] <= viewport['width']
                    and box['y'] + box['height'] <= viewport['height'])

    def _wait_canvas(self, predicate, operation, *, timeout=10):
        deadline = time.monotonic() + timeout
        while True:
            remaining = deadline - time.monotonic()
            if remaining <= 0:
                raise ImageUIError(operation, 'persisted result unknown', mutation_unknown=True)
            try:
                graph = self.workspace.read_canvas(budget_ms=min(self.workspace.read_budget, max(1, int(remaining * 1000))))
            except RedframeWorkspaceError as exc:
                attempt = exc.attempts[-1]
                if attempt['error'] != 'budget' and not _retryable(attempt):
                    raise
            else:
                result = predicate(graph)
                if result is not None:
                    return result
            time.sleep(min(0.2, max(0, deadline - time.monotonic())))

    def find_new_nodes(self, previous_ids, *, generation):
        """Read-only recovery for an interrupted creation/upload; never create again."""
        kind = 'remote-imageGenerationNode' if generation else 'remote-imageNode'
        self.assert_target()
        return [node for node in self.workspace.read_canvas()['nodes']
                if node['id'] not in previous_ids and node.get('type') == kind]

    def _new_node(self, x, y, *, path=None, menu_name='图片', generation_kind='remote-imageGenerationNode',
                  save_baseline=None):
        self.assert_target()
        viewport = self.page.viewport_size
        if not viewport or not 0 < x < viewport['width'] or not 0 < y < viewport['height']:
            raise ValueError('creation point must be in the task viewport')
        # The caller selects a visually checked empty point; no fixed screen coordinate.
        before = {node['id'] for node in self.workspace.read_canvas()['nodes']}
        if save_baseline:
            save_baseline(sorted(before))
        self.page.mouse.click(x, y, button='right')
        kind = 'remote-imageNode' if path else generation_kind
        try:
            menu = self.page.locator('[role="menu"]:visible')
            if path:
                upload = menu.get_by_text('上传', exact=True)
                upload.wait_for(state='visible', timeout=self.timeout)
                with self.page.expect_file_chooser(timeout=self.timeout) as chooser:
                    self._click(upload, 'upload source', mutation=True)
                chooser.value.set_files(str(path), timeout=self.timeout)
            else:
                create = menu.get_by_text(menu_name, exact=True)
                create.wait_for(state='visible', timeout=self.timeout)
                self._click(create, 'create node', mutation=True)
            def created(graph):
                found = [node for node in graph['nodes'] if node['id'] not in before and node.get('type') == kind]
                if len(found) > 1:
                    raise ImageUIError('create image node', 'multiple new nodes; inspect original canvas', mutation_unknown=True)
                return found[0]['id'] if found else None
            return self._wait_canvas(created, 'create image node')
        except ImageUIError as exc:
            exc.previous_node_ids = sorted(before)
            raise
        except Exception as exc:
            error = ImageUIError('upload source' if path else 'create image node', type(exc).__name__, mutation_unknown=True)
            error.previous_node_ids = sorted(before)
            raise error from exc

    def create_image_node(self, x, y):
        return self._new_node(x, y)

    def resume_image_node(self, receipt, save_receipt, x, y):
        """Resume an unsubmitted candidate on its original canvas, or checkpoint its first creation."""
        self.assert_target()
        if receipt.get('image_submission') is not None or receipt.get('image_result') or receipt.get('file'):
            raise ImageUIError('resume image', 'use the existing submission or result')
        for key, value in (('canvas_id', self.canvas_id), ('project_id', self.project_id)):
            if key in receipt and receipt[key] != value:
                raise ImageUIError('resume image', 'receipt belongs to another canvas or project')
        if receipt.get('node_id'):
            node_id = _ident(receipt['node_id'])
            _one(self.workspace.read_canvas()['nodes'], node_id, {'remote-imageGenerationNode'})
            return node_id
        previous = receipt.get('previous_node_ids', receipt.get('before'))
        if previous is not None:
            raise ImageUIError('resume image', 'inspect find_new_nodes on the original canvas before binding a node')

        def checkpoint(ids):
            receipt.update(previous_node_ids=ids, canvas_id=self.canvas_id, project_id=self.project_id)
            save_receipt(receipt)

        receipt['node_id'] = self._new_node(x, y, save_baseline=checkpoint)
        save_receipt(receipt)
        return receipt['node_id']

    def upload_source(self, path, x, y):
        path = Path(path).resolve()
        if not path.is_file() or path.suffix.lower() not in {'.png', '.jpg', '.jpeg', '.webp'}:
            raise ValueError('expected an existing local reference image')
        from PIL import Image
        try:
            with Image.open(path, formats=['PNG', 'JPEG', 'WEBP']) as image:
                image.load()
        except (OSError, ValueError, Image.DecompressionBombError) as exc:
            raise ValueError(f'cannot decode local reference image as PNG, JPEG or WebP: {path}') from exc
        return self._new_node(x, y, path=path)

    def rename(self, node_id, label):
        if not isinstance(label, str) or not label.strip():
            raise ValueError('expected an image node label')
        node = self.focus(node_id)
        self._unique(node.locator('.labelText'), 'edit node name').dblclick(timeout=self.timeout)
        title = self._unique(node.get_by_label('节点名称', exact=True), 'node name')
        title.fill(label, timeout=self.timeout)
        title.press('Enter', timeout=self.timeout)
        self._wait_canvas(lambda g: True if _one(g['nodes'], node_id)['data'].get('label') == label else None, 'rename image node')

    def connect(self, source_id, target_id):
        return self._connect(source_id, target_id, 'remote-imageGenerationNode')

    def _connect(self, source_id, target_id, target_kind, *, source_port='image', save_timeout=10,
                 validate_target=None):
        source_kinds = {
            'image': {'remote-imageNode', 'remote-imageGenerationNode'},
            'video': {'remote-videoNode'},
        }
        if source_port not in source_kinds:
            raise ValueError('需指定已核实的图像或视频源卡端口')
        source_id, target_id = _ident(source_id), _ident(target_id)
        self.assert_target()
        graph = self.workspace.read_canvas()
        _one(graph['nodes'], source_id, source_kinds[source_port])
        if validate_target is None:
            _one(graph['nodes'], target_id, {target_kind})
        else:
            validate_target(graph, target_id)
        def linked(value):
            matches = [edge for edge in value['edges'] if edge.get('source') == source_id
                       and edge.get('sourceHandle') == source_port and edge.get('target') == target_id
                       and edge.get('targetHandle') == 'in']
            if len(matches) > 1:
                raise ImageUIError('wire reference', 'duplicate native edges', node_id=target_id)
            return True if matches else None
        if linked(graph):
            return  # This exact edge is already persisted; do not draw another.
        self.close_dialog()
        ends = [self.page.locator(f'[data-nodeid="{ident}"][data-handleid="{handle}"]')
                for ident, handle in [(source_id, source_port), (target_id, 'in')]]
        boxes = [self._unique(end, 'reference port').bounding_box(timeout=self.timeout) for end in ends]
        if not all(self._inside(box, self.page.viewport_size) for box in boxes):
            self._click(self.page.get_by_role('button', name='适应视图', exact=True), 'fit references')
            boxes = [self._unique(end, 'reference port').bounding_box(timeout=self.timeout) for end in ends]
        if not all(self._inside(box, self.page.viewport_size) for box in boxes):
            raise ImageUIError('wire reference', 'ports must both be visible; inspect task viewport', node_id=target_id)
        try:
            for end in ends:
                end.click(trial=True, timeout=self.timeout)
            boxes = [end.bounding_box(timeout=self.timeout) for end in ends]
            if not all(self._inside(box, self.page.viewport_size) for box in boxes):
                raise ImageUIError('wire reference', 'port positions changed before drag', node_id=target_id)
            start, finish = [(b['x'] + b['width'] / 2, b['y'] + b['height'] / 2) for b in boxes]
            self.page.mouse.move(*start)
            self.page.mouse.down()
            try:
                self.page.mouse.move(*finish, steps=20)
            finally:
                self.page.mouse.up()
            self._wait_canvas(linked, 'wire reference', timeout=save_timeout)
        except ImageUIError:
            raise
        except Exception as exc:
            raise ImageUIError('wire reference', type(exc).__name__, node_id=target_id, mutation_unknown=True) from exc

    def write_prompt(self, node_id, parts):
        """parts: text strings or {index: 1-based menu slot, source_id, text}."""
        if not isinstance(parts, list) or not parts:
            raise ValueError('expected prompt parts')
        expected, mentions = [], []
        for part in parts:
            if isinstance(part, str):
                expected.append(part)
            elif (isinstance(part, dict) and set(part) == {'index', 'source_id', 'text'}
                  and type(part['index']) is int and part['index'] > 0 and isinstance(part['text'], str)):
                source = _ident(part['source_id'])
                token = '{{ref ' + str(part['index']) + '}}'
                expected.extend([token, part['text']])
                mentions.append((source, token))
            else:
                raise ValueError('unsupported prompt part')
        node = self.focus(node_id)
        editor = self._unique(node.get_by_label('生成提示词', exact=True), 'image prompt')
        editor.fill('', timeout=self.timeout)
        def append(text):
            # Menu selection can move focus. End alone only reaches a line end.
            self._click(editor, 'focus image prompt', node_id=node_id)
            editor.evaluate("""element => {
              if (!element.isContentEditable) throw new Error('expected native prompt editor');
              element.focus();
              let end = element;
              const atomic = node => node.nodeType === 1 && node.matches('.imageReference,[contenteditable="false"]');
              while (end.lastChild && !atomic(end)) end = end.lastChild;
              const range = document.createRange();
              if (end.nodeType === Node.TEXT_NODE) range.setStart(end, end.textContent.length);
              else if (end !== element) range.setStartAfter(end);
              else range.selectNodeContents(element);
              range.collapse(false);
              const selection = window.getSelection();
              selection.removeAllRanges();
              selection.addRange(range);
            }""", timeout=self.timeout)
            self.page.keyboard.insert_text(text)
        for part in parts:
            if isinstance(part, str):
                append(part)
            else:
                self._click(node.get_by_role('button', name='引用参考素材', exact=True), 'insert image reference', mutation=True, node_id=node_id)
                self._click(self.page.get_by_text('图片' + str(part['index']), exact=True), 'select image reference', mutation=True, node_id=node_id)
                append(part['text'])
        expected_text = ''.join(expected)
        def saved(graph):
            data = _one(graph['nodes'], node_id, {'remote-imageGenerationNode'})['data']
            return True if data.get('prompt') == expected_text and _prompt_parts(data) == (expected_text, mentions) else None
        self._wait_canvas(saved, 'save image prompt')
        return expected_text

    def set_ratio(self, node_id, ratio):
        if ratio not in {'1:1', '3:4', '4:3', '9:16', '16:9', '2:3', '3:2'}:
            raise ValueError('expected an explicit image ratio')
        node = self.focus(node_id)
        self._click(node.get_by_role('button', name='选择图片比例和分辨率', exact=True), 'open image settings')
        self._click(self.page.get_by_role('button', name=ratio, exact=True), 'set image ratio', mutation=True, node_id=node_id)
        self.page.keyboard.press('Escape')
        self._wait_canvas(lambda g: True if _one(g['nodes'], node_id)['data'].get('ratio') == ratio else None, 'save image ratio')

    def preflight(self, node_id, *, settings, sources, prompt):
        """sources preserves platform slot order; check every native ref N identity."""
        if set(settings) != {'model', 'ratio', 'size', 'count'} or not isinstance(prompt, str) or not prompt:
            raise ValueError('expected exact model, ratio, size, count and prompt')
        if (not isinstance(sources, dict) or any(not _SHA.fullmatch(str(v)) for v in sources.values())):
            raise ValueError('expected source node IDs mapped to original image SHA-256')
        for ident in sources:
            _ident(ident)
        self.assert_target()
        graph = self.workspace.read_canvas()
        target = _one(graph['nodes'], _ident(node_id), {'remote-imageGenerationNode'})
        data = target['data']
        for key, expected in settings.items():
            actual = data.get(key)
            if key == 'model':
                try:
                    actual, expected = json.loads(actual), json.loads(expected)
                except (ValueError, TypeError):
                    raise ImageUIError('image preflight', 'unsupported model identity', node_id=node_id) from None
            if actual != expected:
                raise ImageUIError('image preflight', f'persisted {key} differs from requested value', node_id=node_id)
        text, chips = _prompt_parts(data)
        if text != prompt or data.get('prompt') != prompt or {ident for ident, _ in chips} != set(sources):
            raise ImageUIError('image preflight', 'persisted body/chips differ from actual source set', node_id=node_id)
        order = list(sources)
        for ident, token in chips:
            match = _REF.fullmatch(token)
            if not match or int(match[1]) > len(order) or order[int(match[1]) - 1] != ident:
                raise ImageUIError('image preflight', 'native ref number/source identity differs from platform slot order', node_id=node_id)
        incoming = [edge for edge in graph['edges'] if edge.get('target') == node_id]
        if (len(incoming) != len(sources) or {edge.get('source') for edge in incoming} != set(sources)
                or any(edge.get('sourceHandle') != 'image' or edge.get('targetHandle') != 'in' for edge in incoming)):
            raise ImageUIError('image preflight', 'native image/in edges do not match intended references', node_id=node_id)
        source_nodes = [_one(graph['nodes'], ident) for ident in sources]
        downloaded = self.workspace.digest_image_outputs(source_nodes)
        if any(row.get('error') is not None or row.get('status') != 200
               or row.get('sha256') != sources[row['node_id']] for row in downloaded):
            raise ImageUIError('image preflight', 'actual source image bytes differ or remain unread', node_id=node_id)
        current = self.workspace.read_canvas()
        current_target = _one(current['nodes'], node_id, {'remote-imageGenerationNode'})
        current_edges = [edge for edge in current['edges'] if edge.get('target') == node_id]
        if (current_target['data'] != data or current_edges != incoming
                or any(_one(current['nodes'], node['id'])['data'] != node['data'] for node in source_nodes)):
            raise ImageUIError('image preflight', 'native inputs changed during source checks', node_id=node_id)
        binding = {'node_id': node_id, 'canvas_id': self.canvas_id, 'project_id': self.project_id,
                   'settings': deepcopy(settings), 'sources': dict(sources),
                   'prompt': prompt, 'previous_output': _image_output_path(target)}
        return {**binding, 'input_sha256': hashlib.sha256(json.dumps(binding, ensure_ascii=False, sort_keys=True).encode()).hexdigest()}

    def submit_once(self, node_id, *, settings, sources, prompt, receipt, save_receipt):
        """Persist the attempt in the existing image receipt BEFORE a single click.

        A recorded attempt, even one whose click timed out, only permits query().
        A newly authorized visual revision uses its own existing candidate receipt.
        """
        if not isinstance(receipt, dict) or not callable(save_receipt):
            raise ValueError('supply the existing image receipt and its durable save function')
        if receipt.get('image_submission') is not None or node_id in self._attempted:
            raise ImageUIError('submit image', 'attempt already recorded; query the original node', node_id=node_id)
        self.focus(node_id)
        checked = self.preflight(node_id, settings=settings, sources=sources, prompt=prompt)
        record = {**checked, 'status': 'unknown', 'attempted_at': datetime.now(timezone.utc).isoformat()}
        receipt['image_submission'] = record
        save_receipt(receipt)  # A failed durable write prevents the click.
        self._attempted.add(node_id)
        try:
            node = self.node(node_id, generation=True)
            self._click(node.get_by_role('button', name='生成图片', exact=True), 'submit image', mutation=True, node_id=node_id)
        except Exception as exc:
            raise ImageUIError('submit image', 'submission result unknown; do not replay', node_id=node_id, mutation_unknown=True) from exc
        # Click return alone is not a provider task receipt.
        return deepcopy(record)

    def query(self, receipt, *, timeout=60):
        self.assert_target()
        record = receipt.get('image_submission') if isinstance(receipt, dict) else None
        if not isinstance(record, dict):
            raise ValueError('expected the original image submission receipt')
        if record.get('canvas_id') != self.canvas_id or record.get('project_id') != self.project_id:
            raise ImageUIError('query image', 'receipt belongs to another project/canvas')
        node_id = _ident(record.get('node_id'))
        output = self.workspace.wait_for_image_result(node_id, record.get('previous_output'), timeout=timeout)
        return {'node_id': node_id, 'status': 'output_observed', 'output': output,
                'task_id': None, 'visual_quality_verified': False}


@contextmanager
def image_browser_session(task_browser, *, project_id, canvas_url, action_timeout_ms=7_000):
    """Use the existing task_browser factory to open an isolated task session."""
    _canvas_id(canvas_url)
    with task_browser(redframe=True) as context:
        page = context.new_page()
        try:
            page.set_viewport_size({'width': 3000, 'height': 2200})
            ui = RedframeImageUI(page, project_id, canvas_url, action_timeout_ms=action_timeout_ms)
            page.goto(canvas_url, wait_until='domcontentloaded', timeout=30_000)
            ui.assert_target()
            yield ui
        finally:
            interrupted = sys.exc_info()[0] is not None
            try:
                page.close()
            except Exception:
                if not interrupted:
                    raise


def image_resume_step(project, receipt):
    """Read-only recovery from one candidate's existing receipt."""
    submission = receipt.get('image_submission')
    result = receipt.get('image_result')
    node_ids = {_ident(record['node_id']) for record in (receipt, submission, result)
                if record is not None and record.get('node_id') is not None}
    if len(node_ids) > 1:
        raise ValueError('conflicting node IDs in image receipt')
    file = receipt.get('file')
    if file and result and result.get('status') == 'output_observed':
        from .io import sha256_file
        path = (project / file['path']).resolve()
        if not path.is_relative_to(project):
            raise ValueError('image file outside project')
        if path.is_file() and sha256_file(path) == file['sha256']:
            return 'review_local_file'
    if result and result.get('status') == 'output_observed':
        return 'retrieve_original_output'
    if submission is not None:
        return 'query_original_submission'
    if file or result:
        raise ValueError('incomplete image result; inspect the original receipt')
    if node_ids:
        return 'resume_original_node'
    if 'previous_node_ids' in receipt or 'before' in receipt:
        return 'inspect_creation_on_original_canvas'
    return 'verify_canvas_before_creation'


if __name__ == '__main__':
    import argparse
    parser = argparse.ArgumentParser(description='只读核对单图回执，返回准确目标和下一步')
    parser.add_argument('--project', required=True, type=Path)
    parser.add_argument('--receipt', required=True, type=Path)
    args = parser.parse_args()
    root = args.project.resolve(strict=True)
    path = (root / args.receipt).resolve(strict=True)
    if not path.is_relative_to(root):
        parser.error('回执须在本项目内')
    receipt = json.loads(path.read_bytes())
    print(json.dumps(dict(target=receipt['target'], next_step=image_resume_step(root, receipt),
                          node_id=receipt.get('node_id') or (receipt.get('image_submission') or {}).get('node_id')),
                     ensure_ascii=False))
