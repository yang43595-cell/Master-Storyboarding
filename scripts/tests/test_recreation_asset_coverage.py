from scripts.core.artifacts import digest
from scripts.core.io import write_json
from scripts.core.recreation_segments import PLAN, segment_steps


def test_only_affected_segment_waits_for_the_visible_appearance(tmp_path):
    story = dict(story_paragraphs=[dict(paragraph_id='P')], dialogues=[], asset_requirements=[
        dict(asset_id='AST-A', source_paragraph_ids=['P'], category='CHARACTER', image_decision='需要制作'),
        dict(asset_id='AST-B', source_paragraph_ids=['P'], category='CHARACTER', image_decision='无需独立图'),
    ])
    photo = tmp_path / '01_原始资料/候选脸.png'
    photo.parent.mkdir()
    photo.write_bytes(b'original-face')
    master = tmp_path / '04_资产/母版/board.png'
    master.parent.mkdir(parents=True)
    master.write_bytes(b'confirmed-board')
    motion = tmp_path / '01_原始资料/depth.mp4'
    motion.write_bytes(b'motion')
    def ref(path, **fields):
        return dict(path=str(path.relative_to(tmp_path)), sha256=digest(path), **fields)

    def segment(sid, start, visible, refs):
        return dict(id=sid, mode='depth', frames=[start, start + 10], reference_frames=[0, 10],
                    motion='depth', appearance=refs, visible_asset_ids=visible,
                    paragraph_ids=['P'], dialogue_ids=[])

    plan = dict(narrative_id='N', narrative_sha256='abc', fps=25, first_frame=0, end_frame=30,
                references={'depth': ref(motion, role='depth', frame_count=10), '旧脸': ref(photo),
                            '板': ref(master, asset_id='AST-A', output_id='A-FINAL')},
                segments=[segment('first', 0, ['AST-A'], ['板']), segment('second', 10, ['AST-A'], []),
                          segment('third', 20, ['AST-B'], [])])
    entry = {'artifact_id': 'N', 'checksum': 'abc'}
    images = {'AST-A': {'status': 'confirmed', 'outputs': [
        {'path': str(master), 'sha256': digest(master), 'output_id': 'A-FINAL'}]}}
    def steps():
        write_json(tmp_path / PLAN, plan)
        return {s['status']: s['segment_ids'] for s in segment_steps(tmp_path, story, entry, asset_images=images)}

    assert steps() == {'ready': ['first', 'third'], 'waiting': ['second']}
    plan['segments'][1]['appearance'] = ['板', '旧脸']
    assert steps() == {'ready': ['first', 'third'], 'waiting': ['second']}
    plan['segments'][1]['appearance'] = ['板']
    assert steps() == {'ready': ['first', 'second', 'third']}
    del plan['references']['板']['asset_id'], plan['references']['板']['output_id']
    assert steps() == {'ready': ['first', 'second', 'third']}
    plan['segments'] = [segment('first', 0, [], [])]
    plan['end_frame'] = 10
    del plan['segments'][0]['visible_asset_ids']
    assert steps() == {'waiting': ['first']}


def test_declaring_a_pinned_product_image_cannot_substitute_unrelated_pixels(tmp_path):
    from PIL import Image
    from scripts.core.recreation_assets import matches_product_rendition

    source = tmp_path / 'source.png'
    derivative = tmp_path / 'smaller.png'
    image = Image.new('RGBA', (8, 10), 'teal')
    image.save(source)
    locked = digest(source)
    product = {locked: {'role': 'state', 'path': str(source)}}
    ref = {'path': derivative.name, 'resized_product_source_sha256': locked}
    image.resize((4, 5), Image.Resampling.LANCZOS).save(derivative)
    assert matches_product_rendition(tmp_path, ref, product)
    Image.new('RGBA', (4, 5), 'red').save(derivative)
    assert not matches_product_rendition(tmp_path, ref, product)
