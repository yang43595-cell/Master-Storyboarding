"""Publication-adapted synthetic source contracts; not private-case, image or speech verification."""
import copy
import json
from pathlib import Path
import pytest
from scripts.core import quality_evidence as q


def data():
    return json.loads((Path(__file__).parent/'fixtures/expanded-dialogue-source.json').read_text())


def test_synthetic_phone_fragments_cover_once_and_keep_two_replies():
    d = data()
    result = q.replay_source_audio(d['segments'], d['shot_audio'])
    flat = [p for s in result for p in s['dialogue']]
    assert {p['dialogue_id'] for p in flat} >= {'SYN-D6', 'SYN-D10', 'SYN-D7', 'SYN-D11'}
    assert ''.join(p['text'] for p in flat if p['dialogue_id']=='SYN-D8') == 'Theta iota.'
    assert result[2]['dialogue'][0]['text'].startswith('iota.')
    assert all(p['speaker_id']=='UNKNOWN' for p in flat if p['dialogue_id']=='SYN-D8')


@pytest.mark.parametrize('mutation', ['replay_whole_line','repeat_cross_segment','omit_fragment','speaker','presentation','omit_audio','wrong_shot','reverse_shots'])
def test_phone_counterexamples_rejected(mutation):
    d=data(); seg=d['segments']; audio=seg[0]['source_audio']
    if mutation=='replay_whole_line':
        audio[2]['dialogue'][0]['text']='Alpha beta gamma.'
    elif mutation=='repeat_cross_segment': seg[2]['source_audio'][0]['dialogue'][0]['text']='Theta '+seg[2]['source_audio'][0]['dialogue'][0]['text']
    elif mutation=='omit_fragment': audio[2]['dialogue']=[]
    elif mutation=='speaker': audio[2]['dialogue'][0]['speaker_id']='OTHER'
    elif mutation=='presentation': audio[2]['dialogue'][0]['presentation']='ON_SCREEN_TEXT'
    elif mutation=='omit_audio': seg[0].pop('source_audio')
    elif mutation=='wrong_shot': audio[0]['shot_id']='S99-999'
    else: seg[0]['source_audio'].reverse()
    with pytest.raises(ValueError,match='source_audio'):
        q.replay_source_audio(seg,d['shot_audio'])


def test_same_shot_split_is_legal_without_duplicate_utterance():
    part=dict(dialogue_id='DLG-1',speaker_id='A',presentation='SYNC',text='再见，明天见。')
    shots={'S01-001':[part]}
    def seg(i,text):
        return dict(segment_id=i,source_shots=['S01-001'],source_audio=[dict(shot_id='S01-001',dialogue=[dict(part,text=text)])])
    rows=[seg('G1','再见，'),seg('G2','明天见。')]
    assert len(q.replay_source_audio(rows,shots))==2
    rows[1]['source_audio'][0]['dialogue'][0]['text']=part['text']
    with pytest.raises(ValueError,match='source_audio'): q.replay_source_audio(rows,shots)


def test_context_is_not_an_utterance_source():
    d=data(); before=q.replay_source_audio(d['segments'],d['shot_audio'])
    d['segments'][2]['production_notes']='上一段已称呼，不发声上下文。'
    d['segments'][2]['prompt']='Untrusted prompt prose is not parsed as proof of spoken output.'
    assert q.replay_source_audio(d['segments'],d['shot_audio'])==before


def test_consumer_checks_audio_even_without_scene_states(tmp_path):
    d=data(); video={'generation_segments':d['segments']}
    director={'scenes':[{'shots':[{'shot_id':k,'audio':{'dialogue':v}} for k,v in d['shot_audio'].items()]}]}
    sources=[({},tmp_path/'director.json',director),({},tmp_path/'video.json',video)]
    q.validate_scene_source_mapping([],sources,tmp_path)
    video['generation_segments'][1]['source_audio'][0]['dialogue'][0]['text']='前段又说一遍'
    with pytest.raises(ValueError,match='source_audio'):
        q.validate_scene_source_mapping([],sources,tmp_path)


def test_no_source_audio_declarations_do_not_prove_dialogue_checked(tmp_path):
    # Legacy source lacking director placements has no audio guarantee; not NLP.
    q.validate_scene_source_mapping([], [({},tmp_path/'legacy.json',{'fixture':'legacy'})],tmp_path)


def test_correct_words_or_scoped_use_do_not_override_failed_geometry():
    e={'status':'pass','observer':'synthetic','checked_at':'2026-10-26T16:00:00Z','checks':{
        k:{'result':'pass','observation':'synthetic only','locator':'fixture'} for k in q.IMAGE_ASPECTS}}
    e['checks']['space_objects'].update(result='fail',observation='chair behind couple conflicts with table-side source; purpose label cannot repair geometry')
    with pytest.raises(ValueError,match='space_objects'):
        q._observations(e,q.IMAGE_ASPECTS)


def test_method_replay_records_exact_sources_and_scope(tmp_path):
    # Replaces an unavailable private-workspace audit with controlled source
    # integrity assertions. Does NOT reproduce that private 22-row audit.
    import hashlib
    runtime=Path(__file__).resolve().parents[2]
    methods=[
        ('skills/storyboard-planner/modules/shot-planning.md','未读取不等于缺资料'),
        ('skills/voiceover-to-video-script/modules/video-method.md','不可直接复制成 `exit_state`'),
        ('skills/storyboard-director/references/image-continuity-hard-rules.md','limited-purpose免责'),
    ]
    source=[{'segment_id':f'SYN-{i:02}', 'candidate_prompt':f'Synthetic exact excerpt {i}.'} for i in range(22)]
    path=tmp_path/'synthetic-source.json'
    path.write_text(json.dumps(source),encoding='utf-8')
    checksum=hashlib.sha256(path.read_bytes()).hexdigest()
    rows=[]
    for i,segment in enumerate(source):
        method,heading=methods[i%len(methods)]
        rows.append({'feedback_id':f'SYN-{i:02}', 'evidence_status':'reported_synthetic_only',
            'source':{'path':path.name,'sha256':checksum,'segment_id':segment['segment_id'],
                      'exact_excerpt':segment['candidate_prompt']},
            'method':method+'#'+heading,
            'local_candidate':{'prompt':f'Synthetic candidate {i}.','status':'unreviewed_offline_only',
                               'reference_plan':['synthetic unverified reference']},
            'bad_counterexample':{'prompt':f'Synthetic counterexample {i}.'},
            'method_hit':{'kind':'author_applied_not_automatic_semantic_detection'},
            'unverified':['pixels','speech','native platform']})
    replay={'rows':rows,'generated_media':False,'original_results_changed':False}
    assert len(replay['rows'])==22
    assert not replay['generated_media'] and not replay['original_results_changed']
    for row in replay['rows']:
        src=row['source']; source_path=tmp_path/src['path']
        assert hashlib.sha256(source_path.read_bytes()).hexdigest()==src['sha256']
        segment=next(x for x in json.loads(source_path.read_text()) if x['segment_id']==src['segment_id'])
        assert src['exact_excerpt'] in segment['candidate_prompt']
        method,heading=row['method'].split('#',1)
        assert heading in (runtime/method).read_text()
        assert row['local_candidate']['prompt']!=row['bad_counterexample']['prompt']
        assert row['local_candidate']['status']=='unreviewed_offline_only'
        assert row['method_hit']['kind']=='author_applied_not_automatic_semantic_detection'
        assert row['unverified'] and row['local_candidate']['reference_plan']
    states={r['feedback_id']:r['evidence_status'] for r in replay['rows']}
    assert states['SYN-00'].startswith('reported')
    assert states['SYN-01'].startswith('reported')
    assert states['SYN-02'].startswith('reported')
