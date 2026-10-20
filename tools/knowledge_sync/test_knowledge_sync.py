import importlib.util
import json
import os
from pathlib import Path
import tempfile
import unittest

spec=importlib.util.spec_from_file_location('ks',Path(__file__).with_name('knowledge_sync.py'))
ks=importlib.util.module_from_spec(spec);spec.loader.exec_module(ks)
REPO=Path(os.environ.get('MSB_TEST_REPO', Path(__file__).resolve().parents[2]))
SOURCE_ROOT='/synthetic-fixture/wiki'


def rule(id='MSB-KB-TEST-001',key='test.one',body='看清动作。',priority=1):
    data=dict(id=id,canonical_key=key,kind='rule',status='enabled',authority='wiki',projects=['Master-Storyboarding'],stages=['storyboard-planner'],topics=['camera'],triggers=[],conflicts_with=[],priority=priority,sources=[],applies_when='测试')
    return ('<!-- msb-knowledge\n'+json.dumps(data)+'\n-->\n'+body+'\n^'+id.lower()+'\n<!-- /msb-knowledge -->\n').encode()


class SyncTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.root=Path(self.tmp.name);self.store=self.root/'store'
    def tearDown(self):self.tmp.cleanup()
    def export(self,files=None,**changes):
        directory=self.root/('export-'+str(len(list(self.root.glob('export-*')))));directory.mkdir()
        files=files or {'wiki/concepts/a.md':rule()}
        records=[]
        for name,raw in files.items():
            p=directory/'payload'/name;p.parent.mkdir(parents=True,exist_ok=True);p.write_bytes(raw)
            records.append(dict(path=name,source_id='fixture:'+name,sha256=ks.sha(raw),size=len(raw),mtime='test-only'))
        data=dict(schema_version='1.0',transport='synthetic_fixture',source_root=SOURCE_ROOT,complete=False,scope=list(files),files=records,removals=[],base_snapshot=None,exported_at='synthetic-not-real')
        data.update(changes);ks.atomic_json(directory/'export.json',data);return directory
    def build(self,e,base=None):return ks.build(self.store,e,REPO,base,expected_source_root=SOURCE_ROOT)['snapshot']
    def test_same_bytes_idempotent_even_changed_mtime(self):
        e=self.export();v=self.build(e);d=ks.read_json(e/'export.json');d['files'][0]['mtime']='changed';ks.atomic_json(e/'export.json',d)
        self.assertEqual(v,self.build(e));self.assertEqual(v,self.build(e,v));self.assertEqual(len(list((self.store/'snapshots').iterdir())),1)
    def test_paths_reject_traversal_absolute_windows_and_raw(self):
        e=self.export()
        with self.assertRaisesRegex(ks.SyncError, 'source root differs'):
            ks.inspect_export(e, expected_source_root='/different/wiki')
        for root in ['wiki', '/fixture/../wiki', '/fixture/wiki/']:
            with self.assertRaises(ks.SyncError):
                ks.inspect_export(e, expected_source_root=root)
        for path in ['../evil','/tmp/a','wiki/../a','wiki//a','C:/a','wiki\\a','raw/x','projects/x']:
            with self.subTest(path=path),self.assertRaises(ks.SyncError):ks.safe_path(self.root,path,wiki_only=True)
    def test_symlink_files_parent_and_root_rejected(self):
        e=self.export();p=e/'payload/wiki/concepts/a.md';original=self.root/'original';original.write_bytes(p.read_bytes());p.unlink();p.symlink_to(original)
        with self.assertRaises(ks.SyncError):ks.inspect_export(e,expected_source_root=SOURCE_ROOT)
        link=self.root/'link';link.symlink_to(e,target_is_directory=True)
        with self.assertRaises(ks.SyncError):ks.inspect_export(link,expected_source_root=SOURCE_ROOT)
        with self.assertRaises(ks.SyncError):ks.safe_path(link/'payload','wiki/concepts/a.md')
    def test_hash_and_missing_file_rejected(self):
        e=self.export();p=e/'payload/wiki/concepts/a.md';p.write_bytes(b'changed')
        with self.assertRaises(ks.SyncError):self.build(e)
        p.unlink()
        with self.assertRaises(ks.SyncError):self.build(e)
    def test_incomplete_export_keeps_other_sources(self):
        e=self.export({'wiki/concepts/a.md':rule(),'wiki/concepts/b.md':rule('MSB-KB-TEST-002','test.two')});a=self.build(e)
        b=self.build(self.export({'wiki/concepts/a.md':rule(body='新测试正文。')}),a)
        root,m=ks.validate_snapshot(self.store,b);self.assertTrue((root/'vault/wiki/concepts/b.md').is_file())
        self.assertNotEqual(a,b);ks.validate_snapshot(self.store,a)
    def test_removal_never_deletes_prior_snapshot(self):
        e=self.export();a=self.build(e);d=ks.read_json(e/'export.json');d.update(files=[],removals=d['scope'],complete=False);ks.atomic_json(e/'export.json',d)
        with self.assertRaisesRegex(ks.SyncError,'Incomplete'):self.build(e,a)
        d['complete']=True;ks.atomic_json(e/'export.json',d)
        with self.assertRaisesRegex(ks.SyncError,'dependency review'):self.build(e,a)
        ks.validate_snapshot(self.store,a)
    def test_rule_id_canonical_and_metadata_conflicts(self):
        for other in [rule(body='不同内容。'),rule('MSB-KB-TEST-002','test.one'),rule(priority=3)]:
            with self.subTest(other=other),self.assertRaises(ks.SyncError):self.build(self.export({'wiki/concepts/a.md':rule(),'wiki/concepts/b.md':other}))
    def test_source_id_cannot_silently_rename(self):
        a=self.build(self.export());e=self.export({'wiki/concepts/b.md':rule()});d=ks.read_json(e/'export.json');d['files'][0]['source_id']='fixture:wiki/concepts/a.md';ks.atomic_json(e/'export.json',d)
        with self.assertRaisesRegex(ks.SyncError,'rename'):self.build(e,a)
    def test_same_task_pin_blocks_new_snapshot(self):
        a=self.build(self.export());b=self.build(self.export({'wiki/concepts/a.md':rule(body='新正文。')}),a);project=self.root/'project'
        ks.pin(self.store,a,project,'task-one');ks.pin(self.store,a,project,'task-one')
        with self.assertRaisesRegex(ks.SyncError,'pinned'):ks.pin(self.store,b,project,'task-one')
        ks.pin(self.store,b,project,'task-two')
    def test_task_basis_pin_survives_missing_local_pin_receipt(self):
        a=self.build(self.export());project=self.root/'project';ks.atomic_json(project/'00_项目管理/执行上下文/task_basis.json',{'reading_manifest':{'task_id':'same','vault_root':'/old/vault'}})
        with self.assertRaisesRegex(ks.SyncError,'task_basis'):ks.pin(self.store,a,project,'same')
    def test_snapshot_added_file_and_manifest_tamper_rejected(self):
        a=self.build(self.export());root,_=ks.validate_snapshot(self.store,a);(root/'vault/extra.md').write_text('unexpected')
        with self.assertRaisesRegex(ks.SyncError,'undeclared'):ks.validate_snapshot(self.store,a)
        (root/'vault/extra.md').unlink();m=ks.read_json(root/'manifest.json');m['identity']['retired'].append('fake');ks.atomic_json(root/'manifest.json',m)
        with self.assertRaisesRegex(ks.SyncError,'identity'):ks.validate_snapshot(self.store,a)
    def test_export_baseline_conflict(self):
        e=self.export(base_snapshot='1'*64)
        with self.assertRaisesRegex(ks.SyncError,'base_snapshot'):self.build(e)
    def test_failed_sync_retains_last_snapshot_without_claiming_offline(self):
        a=self.build(self.export());r=ks.mark_failure(self.store,'transport failed')
        self.assertEqual(r['last_available_snapshot'],a);self.assertEqual(r['live_mac_state'],'not_checked');ks.pin(self.store,a,self.root/'project','offline-use')
    def test_verified_baseline_is_inherited_and_not_a_wiki_upload(self):
        source=self.root/'baseline';path=source/'projects/Master-Storyboarding/method.md';path.parent.mkdir(parents=True);path.write_bytes(b'historical verified method')
        row=dict(path=path.relative_to(source).as_posix(),size=path.stat().st_size,sha256=ks.sha(path.read_bytes()))
        receipt=self.root/'baseline.json';ks.atomic_json(receipt,dict(kind='verified_P0_baseline',files=[row]))
        a=ks.seed_baseline(self.store,source,receipt,REPO)['snapshot'];b=self.build(self.export(),a)
        root,_=ks.validate_snapshot(self.store,b);self.assertEqual((root/'vault'/row['path']).read_bytes(),path.read_bytes())
        path.write_bytes(b'tampered')
        with self.assertRaisesRegex(ks.SyncError,'Baseline bytes'):ks.seed_baseline(self.store,source,receipt,REPO)
    def test_source_change_during_execution_rejected_on_next_check(self):
        a=self.build(self.export());root,_=ks.validate_snapshot(self.store,a)
        (root/'vault/wiki/concepts/a.md').write_bytes(b'changed')
        with self.assertRaisesRegex(ks.SyncError,'Snapshot bytes'):ks.pin(self.store,a,self.root/'project','task')
    def test_partial_update_cannot_introduce_case_or_unicode_alias(self):
        a=self.build(self.export())
        with self.assertRaisesRegex(ks.SyncError,'normalization'):
            self.build(self.export({'wiki/concepts/A.md':b'plain page'}),a)
        first=self.build(self.export({'wiki/concepts/caf\u00e9.md':b'plain page'}))
        with self.assertRaisesRegex(ks.SyncError,'normalization'):
            self.build(self.export({'wiki/concepts/cafe\u0301.md':b'plain page'}),first)
    def test_actual_build_failure_updates_status_and_keeps_last_success(self):
        a=self.build(self.export());before=ks.status(self.store)
        e=self.export();(e/'payload/wiki/concepts/a.md').write_bytes(b'broken')
        with self.assertRaises(ks.SyncError):self.build(e,a)
        after=ks.status(self.store)
        self.assertEqual(after['last_attempt_status'],'failed');self.assertEqual(after['last_success'],before['last_success'])
        self.assertEqual(after['last_available_snapshot'],a);ks.validate_snapshot(self.store,a)
    def test_other_source_root_cannot_inherit_wiki_base(self):
        a=self.build(self.export())
        e=self.export({'wiki/concepts/b.md':rule('MSB-KB-TEST-002','test.two')},source_root='/other-fixture/wiki')
        with self.assertRaisesRegex(ks.SyncError,'Base wiki source root'):
            ks.build(self.store,e,REPO,a,expected_source_root='/other-fixture/wiki')
        _,m=ks.validate_snapshot(self.store,a)
        self.assertEqual(m['identity']['wiki_source_root'],SOURCE_ROOT)
    def test_other_source_root_cannot_reuse_identical_snapshot(self):
        a=self.build(self.export())
        e=self.export(source_root='/other-fixture/wiki')
        with self.assertRaisesRegex(ks.SyncError,'Store is bound'):
            ks.build(self.store,e,REPO,expected_source_root='/other-fixture/wiki')
        _,m=ks.validate_snapshot(self.store,a)
        self.assertEqual(m['provenance']['source_root'],SOURCE_ROOT)
        self.assertEqual(len(list((self.store/'snapshots').iterdir())),1)
    def test_complete_export_cannot_omit_member(self):
        e=self.export(complete=True,scope=['wiki/concepts/a.md','wiki/concepts/missing.md'])
        with self.assertRaisesRegex(ks.SyncError,'unexplained'):self.build(e)

if __name__=='__main__':unittest.main(verbosity=2)
