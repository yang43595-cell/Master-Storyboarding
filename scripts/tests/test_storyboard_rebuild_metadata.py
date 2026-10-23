"""Narrow metadata compatibility without granting production approval."""
import json, unittest
from pathlib import Path
from scripts.core.runtime_deps import require_runtime_deps
require_runtime_deps(Path(__file__).resolve().parents[2])
import jsonschema
S=json.loads((Path(__file__).resolve().parents[2]/'schemas/storyboard-package.schema.json').read_text())
class RebuildMetadataTests(unittest.TestCase):
 def valid(self, field, value):
  target=S['properties']['rebuild_provenance'] if field=='rebuild' else S['$defs'][field[0]]['properties'][field[1]]
  return not list(jsonschema.Draft202012Validator({'$schema':S['$schema'],'$defs':S['$defs'],**target}).iter_errors(value))
 def base(self):
  return {'baseline_path':'03_导演/R04.json','baseline_sha256':'a'*64,'reason':'Verified reconstruction, not lost revision.','completed_scene_ids':['S01'],'unreviewed_scene_ids':[],'video_generation_authorized':False,'downstream_ready':False,'pending_checks':['Candidate needs review'],'text_review':'Text check only.'}
 def test_valid_and_optional(self):
  self.assertTrue(self.valid('rebuild',self.base()));self.assertNotIn('rebuild_provenance',S['required']);self.assertFalse(S['additionalProperties'])
  for group,field in [('directorRules','focal_length_basis'),('spaceRules','care_staging'),('timingBasis','source_sha256')]:
   self.assertNotIn(field,S['$defs'][group]['required']);self.assertFalse(S['$defs'][group]['additionalProperties'])
 def test_invalid_rebuild(self):
  for key,bad in [('baseline_sha256','bad'),('baseline_path',4),('reason',None),('completed_scene_ids',['S1']),('unreviewed_scene_ids',['S01','S01']),('pending_checks',[False]),('text_review',[]),('invented_confirmation',True)]:
   d=self.base();d[key]=bad;self.assertFalse(self.valid('rebuild',d),key)
 def test_no_authority(self):
  for key in ['video_generation_authorized','downstream_ready']:
   d=self.base();d[key]=True;self.assertFalse(self.valid('rebuild',d))
 def test_text(self):
  for field in [('directorRules','focal_length_basis'),('spaceRules','care_staging')]:
   self.assertTrue(self.valid(field,'Design basis, not confirmation.'))
   for bad in ['',None,2,{},[]]:self.assertFalse(self.valid(field,bad))
 def test_hash(self):
  field=('timingBasis','source_sha256');self.assertTrue(self.valid(field,'b'*64))
  for bad in ['confirmed','b'*63,'g'*64,None,True]:self.assertFalse(self.valid(field,bad))
  self.assertEqual(S['$defs']['timingBasis']['properties']['status']['enum'],['authoritative','not_evaluable'])
if __name__=='__main__':unittest.main()
