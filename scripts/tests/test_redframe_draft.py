from contextlib import contextmanager
from types import SimpleNamespace
import sys

import pytest

from scripts import redframe_draft


def test_retest_refreshes_in_the_same_browser_after_resave(monkeypatch):
    calls = []
    node = {'id': 'sample', 'type': 'remote-videoGenerationNode', 'position': {'x': 0, 'y': 0}}
    graph = {'nodes': [node], 'edges': []}
    frozen = {'target_url': 'canvas', 'target_project_id': 'project', 'revision': 'R01',
              'drafts': [{'node_id': 'sample', 'edges': [], 'source_nodes': []}]}
    browser = object()

    class Workspace:
        read_budget = 30_000
        page = SimpleNamespace(reload=lambda **_: calls.append('refresh'))

        def read_canvas(self, **kwargs):
            calls.append('read')
            return graph

        def write_canvas(self, expected, document):
            assert expected == document == graph
            calls.append('save')

        def digest_slots(self, nodes):
            assert nodes == [node]
            return {'sample': []}

    @contextmanager
    def open_once(factory, target, project):
        assert (factory, target, project) == (browser, 'canvas', 'project')
        calls.append('open')
        yield Workspace()
        calls.append('close')

    monkeypatch.setattr(sys, 'path', sys.path.copy())
    monkeypatch.setitem(sys.modules, 'headless_edge', SimpleNamespace(task_browser=browser))
    monkeypatch.setattr(redframe_draft, 'open_workspace', open_once)
    monkeypatch.setattr(redframe_draft, 'validate_submission', lambda *args, **kwargs: None)
    monkeypatch.setattr(redframe_draft, 'source_capabilities', lambda *args: {})
    monkeypatch.setattr(redframe_draft, 'before_write_readback', lambda *args: {})
    monkeypatch.setattr(redframe_draft, 'compare_baseline', lambda *args: {'ok': True})
    monkeypatch.setattr(redframe_draft, 'native_draft', lambda *args: {'node_id': 'sample'})
    monkeypatch.setattr(redframe_draft, 'compare', lambda *args, **kwargs: {'ok': True})

    result = redframe_draft.retest('project', frozen, {}, graph, [{'id': 'sample', 'w': 1, 'h': 1}], resave=True)

    assert result['ok']
    assert calls == ['open', 'read', 'save', 'refresh', 'read', 'close']


def test_first_saved_node_waits_for_the_original_id(monkeypatch):
    monkeypatch.setattr(redframe_draft.time, 'sleep', lambda _: None)
    node = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}
    reads = iter([{'nodes': [dict(node, id='some-other-node')]}, {'nodes': [node]}])

    class Workspace:
        read_budget = 30_000

        def read_canvas(self, *, budget_ms):
            return next(reads)

    assert redframe_draft.verify_saved(Workspace(), {'original': None}) == {'nodes': [node]}


def test_persistence_rejects_a_stale_existing_node_on_a_single_read():
    desired = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}
    calls = []

    class Workspace:
        def read_canvas(self):
            calls.append('read')
            return {'nodes': [dict(desired, data={'prompt': 'old'})]}

    with pytest.raises(ValueError, match='原节点 ID.*original'):
        redframe_draft.verify_saved(Workspace(), {'original': desired}, timeout=0)
    assert calls == ['read']


@pytest.mark.parametrize(('timeout', 'read_seconds', 'expected_budgets', 'expected_pauses'), [
    (15, 10, [15_000, 4_000], [1]),
    (0.5, 0, [500], [0.5]),
])
def test_persistence_reads_only_within_the_remaining_poll_budget(
        monkeypatch, timeout, read_seconds, expected_budgets, expected_pauses):
    now, budgets, pauses = 0, [], []

    def sleep(seconds):
        nonlocal now
        pauses.append(seconds)
        now += seconds

    class Workspace:
        read_budget = 30_000

        def read_canvas(self, *, budget_ms):
            nonlocal now
            budgets.append(budget_ms)
            now += min(read_seconds, budget_ms / 1000)
            return {'nodes': []}

    monkeypatch.setattr(redframe_draft.time, 'monotonic', lambda: now)
    monkeypatch.setattr(redframe_draft.time, 'sleep', sleep)
    with pytest.raises(ValueError, match='原节点 ID.*original'):
        redframe_draft.verify_saved(Workspace(), {'original': None}, timeout=timeout)
    assert budgets == expected_budgets
    assert pauses == expected_pauses
    assert now == timeout


def test_two_drafts_cannot_reserve_the_same_default_position():
    existing = {'earlier': (0, 0, 220, 262)}
    planned = {'first': (6656, 2800, 220, 262), 'second': (6656, 2800, 220, 262)}
    with pytest.raises(ValueError, match='second 与 first'):
        redframe_draft.verify_placements(existing, planned)
    planned['second'] = (6944, 2800, 220, 262)
    redframe_draft.verify_placements(existing, planned)


def test_reusing_measured_geometry_requires_current_node_ids():
    graph = {'nodes': [{'id': 'kept', 'position': {'x': 0, 'y': 0}},
                       {'id': 'new', 'position': {'x': 250, 'y': 0}}]}
    with pytest.raises(ValueError, match='节点集合'):
        redframe_draft.measured_placements(graph, {'kept': (220, 262)}, ['kept'])
