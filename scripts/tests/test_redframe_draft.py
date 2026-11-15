import pytest

from scripts import redframe_draft


def test_first_saved_node_waits_for_the_original_id(monkeypatch):
    monkeypatch.setattr(redframe_draft.time, 'sleep', lambda _: None)
    node = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}
    reads = iter([{'nodes': [dict(node, id='some-other-node')]}, {'nodes': [node]}])

    class Workspace:
        def read_canvas(self):
            return next(reads)

    assert redframe_draft.verify_saved(Workspace(), {'original': None}) == {'nodes': [node]}


def test_persistence_rejects_a_stale_existing_node():
    desired = {'id': 'original', 'type': 'remote-videoGenerationNode', 'data': {'prompt': 'new'}}

    class Workspace:
        def read_canvas(self):
            return {'nodes': [dict(desired, data={'prompt': 'old'})]}

    with pytest.raises(ValueError, match='原节点 ID.*original'):
        redframe_draft.verify_saved(Workspace(), {'original': desired}, timeout=0)


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
