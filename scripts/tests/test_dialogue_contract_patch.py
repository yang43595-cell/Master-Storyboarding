"""Focused tests importing the real runtime module; NOT full repo/schema integration.
"""
import copy
import hashlib
import json
from pathlib import Path
import sys
import tempfile
import unittest

sys.path.insert(0, str(Path(__file__).resolve().parents[1]))
from scripts.core.story_sources import validate_dialogue_review as validate

class DialogueContractTests(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.addCleanup(self.tmp.cleanup)
        self.root = Path(self.tmp.name)
        evidence = self.root / 'source.json'
        evidence.write_text(json.dumps(['不是你都当孩子妈了', '又不是小孩', '还过什么生日啊']))
        digest = hashlib.sha256(evidence.read_bytes()).hexdigest()
        self.record = {'sources': [{'source_id': f'SRC-{i}', 'kind': 'subtitle_verbatim', 'text': t, 'evidence_path': 'source.json', 'evidence_sha256': digest, 'locator': f'/{i}', 'text_pointer': f'/{i}', 'producer': 'fixture'} for i,t in enumerate(json.loads(evidence.read_text()))], 'dialogues': [{'dialogue_id': 'DLG-001', 'speaker_id': 'HUSBAND', 'speaker_status': 'inferred_unverified', 'origin': {'kind': 'source_reproduction'}, 'text': '不是，你都当孩子妈了，又不是小孩，还过什么生日啊？', 'source_ids': ['SRC-0', 'SRC-1', 'SRC-2'], 'source_assembly': {'source_ids': ['SRC-0','SRC-1','SRC-2'], 'reason': 'fixture same turn declaration, not proof', 'reference': 'fixture 3-7s', 'reviewer': 'fixture'}}]}
    def test_assembly_preserves_sources_and_stays_unresolved(self):
        before = copy.deepcopy(self.record['sources'])
        result = validate(self.record, self.root)
        self.assertEqual(result['unresolved_ids'], ['DLG-001'])
        self.assertEqual(self.record['sources'], before)
        self.assertEqual(result['conflicts'], [])
    def test_punctuation_only_without_assembly_or_proposal(self):
        row = self.record['dialogues'][0]
        row.pop('source_assembly'); row['source_ids']=['SRC-0']; row['text']='不是，你都当孩子妈了。'
        self.assertEqual(validate(self.record,self.root)['unresolved_ids'],['DLG-001'])
    def bind_narrative(self):
        from scripts.core.project_state import new_project_state, write_project_state
        from scripts.core.candidate_test import configure
        write_project_state(self.root, new_project_state(current_stage='creative-director'))
        configure(self.root, 'synthetic-run', 'synthetic fixture only', [])
        row=self.record['dialogues'][0]
        path=self.root/'review.json';path.write_text(json.dumps(self.record))
        return {'schema_version':'2.0','project_id':'MSB-TEST','revision':'R01','status':'candidate',
            'objective':'synthetic only','locked_facts':[],
            'story_paragraphs':[{'paragraph_id':'STP-01','text':f'{row["speaker_id"]}（{row["dialogue_id"]}）："{row["text"]}"'}],
            'dialogues':[{'dialogue_id':row['dialogue_id'],'speaker_id':row['speaker_id'],'text':row['text'],'source_paragraph_id':'STP-01','status':'draft','source_ref':'review.json'}],
            'asset_requirements':[], 'change_summary':[],'open_questions':[],
            'dialogue_evidence_path':'review.json','dialogue_evidence_sha256':hashlib.sha256(path.read_bytes()).hexdigest()}
    def test_real_schema_candidate_retains_unknown_and_production_block(self):
        from scripts.core.story_sources import validate_story_sources
        self.record['dialogues'][0].update(speaker_id='UNKNOWN',speaker_status='unresolved')
        n=self.bind_narrative()
        result=validate_story_sources(n,project_root=self.root,candidate_test=True)
        self.assertTrue(result['production_blocked'])
        self.assertEqual(result['unresolved_dialogue_ids'],['DLG-001'])
        with self.assertRaisesRegex(ValueError,'未核对白'): validate_story_sources(n,project_root=self.root)
    def test_body_and_table_wrong_together_fail_against_evidence(self):
        from scripts.core.story_sources import validate_story_sources
        n=self.bind_narrative()
        n['dialogues'][0]['speaker_id']='WIFE'
        n['story_paragraphs'][0]['text']=n['story_paragraphs'][0]['text'].replace('HUSBAND','WIFE')
        with self.assertRaisesRegex(ValueError,'说话人'): validate_story_sources(n,project_root=self.root,candidate_test=True)
    def test_asr_alternatives_cannot_be_assembled(self):
        self.record['sources'][0]['kind']='asr_hypothesis'
        with self.assertRaisesRegex(ValueError,'不能拼接 ASR'): validate(self.record,self.root)
    def test_source_order_change_rejected(self):
        self.record['dialogues'][0]['source_assembly']['source_ids'].reverse()
        with self.assertRaisesRegex(ValueError,'不能改词'): validate(self.record,self.root)
    def test_numeric_punctuation_is_content(self):
        from scripts.core.story_sources import _dialogue_comparison_text as compare
        for original, changed in [('199.9','1999'),('1/2','12'),('12:30','1230'),('-3','3'),('3%','3'),('1,000','1000'),('－3','3')]:
            with self.subTest(original=original): self.assertNotEqual(compare(original),compare(changed))
        self.assertEqual(compare('售价199.9元。'),compare('售价 199.9 元！'))
    def test_numeric_change_cannot_take_reproduction_path(self):
        for original, changed in [('199.9元','1999元'),('1/2','12'),('-3%','3')]:
            with self.subTest(original=original):
                path=self.root/'number.json';path.write_text(json.dumps([original]))
                src=dict(self.record['sources'][0],text=original,evidence_path='number.json',evidence_sha256=hashlib.sha256(path.read_bytes()).hexdigest(),text_pointer='/0')
                row=dict(self.record['dialogues'][0],text=changed,source_ids=[src['source_id']])
                row.pop('source_assembly')
                with self.assertRaisesRegex(ValueError,'解释修订'): validate({'sources':[src],'dialogues':[row]},self.root)
    def test_word_change_rejected(self):
        self.record['dialogues'][0]['text'] = '不是你还小呢'
        with self.assertRaisesRegex(ValueError,'不能改词'): validate(self.record,self.root)
    def test_missing_same_turn_reference_rejected(self):
        self.record['dialogues'][0]['source_assembly']['reference']=''
        with self.assertRaisesRegex(ValueError,'同轮核对依据'): validate(self.record,self.root)
    def test_source_bytes_cannot_be_changed(self):
        self.record['sources'][0]['text']='不是，你都当孩子妈了。'
        with self.assertRaisesRegex(ValueError,'原始证据文字被改写'): validate(self.record,self.root)
    def test_text_only_verification_does_not_resolve_speaker(self):
        row=self.record['dialogues'][0]
        row['verification']={'scope':'dialogue','dialogue_id':row['dialogue_id'],'text':row['text'],'method':'user_confirmation','reference':'synthetic fixture only','reviewer':'fixture'}
        self.assertEqual(validate(self.record,self.root)['unresolved_ids'],['DLG-001'])
        row['verification']['speaker_id']=row['speaker_id']
        self.assertEqual(validate(self.record,self.root)['unresolved_ids'],['DLG-001'])
        row['speaker_status']='verified'
        self.assertEqual(validate(self.record,self.root)['unresolved_ids'],[])
    def test_speaker_drift_rejected(self):
        row=self.record['dialogues'][0]
        row['verification']={'scope':'dialogue','dialogue_id':row['dialogue_id'],'text':row['text'],'speaker_id':'WIFE','method':'user_confirmation','reference':'synthetic fixture only','reviewer':'fixture'}
        with self.assertRaisesRegex(ValueError,'说话人与当前行不一致'): validate(self.record,self.root)
    def test_machine_method_not_human_verification(self):
        row=self.record['dialogues'][0]
        row['verification']={'scope':'dialogue','dialogue_id':row['dialogue_id'],'text':row['text'],'speaker_id':row['speaker_id'],'method':'asr_agreement','reference':'fixture','reviewer':'fixture'}
        with self.assertRaisesRegex(ValueError,'核对记录须绑定'): validate(self.record,self.root)

if __name__ == '__main__': unittest.main()
