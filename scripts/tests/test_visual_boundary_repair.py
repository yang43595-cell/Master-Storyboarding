"""Offline boundary cases. All receipts are ISOLATED TEST FIXTURES, never user approval."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import patch
from PIL import Image
from test_visual_asset_projects import fixture, plan_result
import visual_asset_product_usage as usage


class VisualBoundaryRepair(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory(prefix='ISOLATED_VISUAL_FIXTURE_')
        self.base = Path(self.temp.name)
        self.lib, self.p, self.asset = fixture(self.base)
        self.service = self.lib.projects

    def tearDown(self):
        self.temp.cleanup()

    def person(self, image_decision='需要制作'):
        path = Path(self.p['path']) / 'narrative.json'
        n = json.loads(path.read_text())
        req = n['asset_requirements'][0]
        req.update(asset_id='AST-PERSON', entity_id='CHR-PERSON', category='CHARACTER', character_type='HUMAN', name='一次配角',
                   identity_constraints=['只出现一次'], required_states=['常态'],
                   image_decision=image_decision, image_reason='隔离fixture：单张或镜内描述足够')
        n['asset_requirements'] = [req]
        path.write_text(json.dumps(n, ensure_ascii=False))
        return self.service.sync(self.p['id'])

    def test_human_single_route_enters_without_wardrobe(self):
        self.person()
        data = self.service.action({'action':'character-output-mode', 'project_id':self.p['id'],
                                   'ast_id':'AST-PERSON', 'mode':'single'})
        self.assertFalse(data['rows'][0]['uses_wardrobe'])
        data = self.service.prepare_reuse({'project_id':self.p['id'], 'ast_id':'AST-PERSON'})
        self.assertEqual([s['kind'] for s in data['rows'][0]['job']['steps']], ['人物单图'])

    def test_single_selection_export_consumer_and_rework_gate(self):
        from core.asset_refs import load_asset_list, require_images
        self.person()
        original = (Path(self.p['path']) / 'narrative.json').read_bytes()
        self.service.action({'action':'character-output-mode', 'project_id':self.p['id'], 'ast_id':'AST-PERSON', 'mode':'single'})
        data = self.service.prepare_reuse({'project_id':self.p['id'], 'ast_id':'AST-PERSON'})
        job = data['rows'][0]['job']
        path = self.base / 'ISOLATED_TEST_person.png'
        Image.new('RGB', (320, 480), 'green').save(path)
        a, _ = self.lib.import_file(path, category='人物单图')
        self.service.select({'job_id':job['id'], 'index':0, 'asset_id':a['id']})
        with self.assertRaisesRegex(ValueError, '逐项确认'):
            self.service.export({'project_id':self.p['id'], 'confirm':True})
        self.assertFalse(require_images(Path(self.p['path']), ['AST-PERSON@state0_view0'])[0])
        # ISOLATED TEST FIXTURE: explicit test-only confirmation; no production receipt.
        self.service.confirm({'job_id':job['id'], 'index':0, 'asset_id':a['id'], 'confirm':True})
        a=self.lib.get(a['id']); backup=self.base/'ISOLATED_TEST_backup.png'; shutil.copy2(a['original'],backup)
        a['backup']={'status':'saved','path':str(backup)}; self.lib.save(a)
        result=self.service.export({'project_id':self.p['id'], 'confirm':True})
        self.assertEqual(result['count'],1)
        manifest=json.loads(Path(result['manifest']).read_text())
        out=manifest['assets'][0]['confirmed_outputs'][0]
        exact='AST-PERSON@'+out['output_id']
        self.assertTrue(require_images(Path(self.p['path']), [exact])[0])
        self.assertEqual(len(load_asset_list(Path(self.p['path']))['items'][0]['image']['outputs']),1)
        prior=Path(result['manifest']).read_bytes()
        self.service.reopen({'job_id':job['id'], 'index':0})
        self.assertFalse(require_images(Path(self.p['path']), [exact])[0])
        self.assertEqual(Path(result['manifest']).read_bytes(),prior)
        self.assertEqual((Path(self.p['path'])/'narrative.json').read_bytes(),original)
        self.assertTrue(self.lib.get(job['id'],'jobs')['steps'][0]['history'])

    def test_single_new_candidate_request_uses_character_method_without_extra_sources(self):
        self.person()
        args={'project_id':self.p['id'],'ast_id':'AST-PERSON'}
        self.service.character_output_mode({**args,'mode':'single'})
        job=self.service.prepare_reuse(args)['rows'][0]['job']
        self.service.production_route({**args,'route':'build'})
        job=self.lib.get(job['id'],'jobs')
        request=self.service.request(job,0)
        self.assertEqual(request['kind'],'人物单图')
        self.assertTrue(request['allow_unreferenced_design'])
        self.assertEqual(request['reference_paths'],[])
        self.assertIsNone(request['character_outfit'])
        self.assertEqual(self.service.skill.method_request(request)['asset_method_scopes'],{'character':True})
        package=self.service.skill.build_task_package(request)
        template=package['production_item']['template']
        self.assertEqual(template['status'],'category-guide')
        self.assertIn('人物单图',json.dumps(package,ensure_ascii=False))
        self.assertNotIn('character_workflow',job)

    def test_switching_character_depth_keeps_old_job_and_stops_single_generation_until_selected(self):
        self.person()
        args={'project_id':self.p['id'],'ast_id':'AST-PERSON'}
        self.service.character_output_mode({**args,'mode':'single'})
        job=self.service.prepare_reuse(args)['rows'][0]['job']
        with self.assertRaises(ValueError): self.service.request(job,0)
        old=copy.deepcopy(job)
        data=self.service.character_output_mode({**args,'mode':'wardrobe'})
        self.assertEqual(self.lib.get(job['id'],'jobs'),old)
        self.assertIn(job['id'],data['rows'][0]['job_history'])
        self.assertIsNone(data['rows'][0]['job'])

    def test_adding_reference_preserves_two_view_contract(self):
        result=plan_result(); result['rows'][0]['outputs']=result['rows'][0]['outputs'][:2]
        result['rows'][0]['outputs'][1].update(view='反向图',dependencies=['view0'])
        self.service.apply_plan(self.p,self.p['rows'],result,{'fixture':True})
        data=self.service.scene_reference({'project_id':self.p['id'],'ast_id':'AST-ROOM','asset_id':self.asset['id']})
        steps=data['rows'][0]['job']['steps']
        self.assertEqual([s['view'] for s in steps],['45°','反向图'])
        self.assertEqual(steps[1]['dependencies'],['view0'])

    def test_four_view_explicit_graph_and_budget_remain(self):
        result=plan_result(); outputs=result['rows'][0]['outputs']
        for output in outputs[1:]: output['dependencies']=['view0']
        self.service.apply_plan(self.p,self.p['rows'],result,{'fixture':True})
        job=self.lib.get(self.service.get(self.p['id'])['rows'][0]['job_id'],'jobs')
        self.assertEqual([s['dependencies'] for s in job['steps']],[[],['view0'],['view0'],['view0']])
        with tempfile.TemporaryDirectory() as tmp:
            lib,p,_=fixture(Path(tmp)); result=plan_result(); slot=result['rows'][1]['outputs'][0]
            result['rows'][1]['outputs']=[{**copy.deepcopy(slot),'key':'slot'+str(i)} for i in range(17)]
            with self.assertRaisesRegex(ValueError,'16'):
                lib.projects.apply_plan(p,p['rows'],result,{'fixture':True})

    def test_partial_scene_graph_rejects_each_missing_supplement_source(self):
        for views in (['45°','正面','反面','俯视'], ['母图','侧向','背面'], ['母图','背面']):
            with self.subTest(views=views), tempfile.TemporaryDirectory() as tmp:
                lib,p,_=fixture(Path(tmp)); result=plan_result(); slots=result['rows'][0]['outputs'][:len(views)]
                for i,(slot,view) in enumerate(zip(slots,views)):
                    slot.update(view=view,dependencies=['view0'] if i==1 and len(views)>2 else [])
                result['rows'][0]['outputs']=slots
                with self.assertRaisesRegex(ValueError,'场景.*来源|场景视角.*绑定'):
                    lib.projects.apply_plan(p,p['rows'],result,{'fixture':True})
                self.assertTrue(all(row['job_id'] is None for row in lib.projects.get(p['id'])['rows']))

    def test_saved_missing_scene_source_cannot_bypass_request_selection_confirmation_or_rework(self):
        for views in (['45°','正面','反面','俯视'], ['母图','侧向','背面'], ['母图','背面']):
            with self.subTest(views=views), tempfile.TemporaryDirectory() as tmp:
                lib,p,asset=fixture(Path(tmp)); service=lib.projects; result=plan_result()
                slots=result['rows'][0]['outputs'][:len(views)]
                for i,(slot,view) in enumerate(zip(slots,views)):
                    slot.update(view=view,dependencies=['view0'] if i else [])
                result['rows'][0]['outputs']=slots
                service.apply_plan(p,p['rows'],result,{'fixture':True})
                job=lib.get(service.get(p['id'])['rows'][0]['job_id'],'jobs'); index=len(views)-1
                # ISOLATED TEST FIXTURE: simulate a saved graph accepted by the old bug.
                job['steps'][index]['dependencies']=[]; lib.save(job,'jobs')
                self.assertEqual(service.request(job,0)['reference_paths'],[])
                with self.assertRaisesRegex(ValueError,'场景.*来源'):
                    service.request(job,index)
                args={'job_id':job['id'],'index':index,'asset_id':asset['id']}
                with self.assertRaisesRegex(ValueError,'场景.*来源'): service.select(args)
                service.select({**args,'as_reference':True})
                job=lib.get(job['id'],'jobs')
                with self.assertRaisesRegex(ValueError,'场景.*来源'): service.request(job,index)
                service.clear_references(args)
                job=lib.get(job['id'],'jobs'); step=job['steps'][index]
                step.update(status='awaiting_review',outputs=[asset['id']],selected=asset['id']); lib.save(job,'jobs')
                with self.assertRaisesRegex(ValueError,'场景.*来源'): service.confirm({**args,'confirm':True})
                service.reopen(args)
                with self.assertRaisesRegex(ValueError,'场景.*来源'): service.request(lib.get(job['id'],'jobs'),index)

    def test_two_supplements_share_reused_confirmed_master_without_sibling_dependency(self):
        result=plan_result(); slots=result['rows'][0]['outputs'][:3]
        for i,(slot,view) in enumerate(zip(slots,['已有母图','侧向','背面'])):
            slot.update(view=view,dependencies=['view0'] if i else [])
        result['rows'][0]['outputs']=slots
        self.service.apply_plan(self.p,self.p['rows'],result,{'fixture':True})
        job=self.lib.get(self.service.get(self.p['id'])['rows'][0]['job_id'],'jobs')
        original=Path(self.asset['original']).read_bytes()
        self.service.select({'job_id':job['id'],'index':0,'asset_id':self.asset['id']})
        with self.assertRaisesRegex(ValueError,'确认前置'): self.service.request(self.lib.get(job['id'],'jobs'),1)
        # ISOLATED TEST FIXTURE: project confirmation of an existing source, no generation.
        self.service.confirm({'job_id':job['id'],'index':0,'confirm':True})
        job=self.lib.get(job['id'],'jobs')
        for i in (1,2):
            request=self.service.request(job,i)
            self.assertEqual([r['id'] for r in request['reference_records']],[self.asset['id']])
            self.assertEqual(job['steps'][i]['dependencies'],['view0'])
        self.assertEqual(job['steps'][1]['status'],'waiting')
        self.assertEqual(Path(self.asset['original']).read_bytes(),original)

    def test_scene_sparse_actual_sources_confirm_and_rework_without_uploading_lineage(self):
        result=plan_result(); slots=result['rows'][0]['outputs'][:3]
        for i,(slot,view) in enumerate(zip(slots,['母图','斜侧','背面'])):
            slot.update(view=view,dependencies=[f'view{i-1}'] if i else [])
        result['rows'][0]['outputs']=slots
        self.service.apply_plan(self.p,self.p['rows'],result,{'fixture':True})
        job=self.lib.get(self.service.get(self.p['id'])['rows'][0]['job_id'],'jobs')
        assets=[]
        for i,color in enumerate(['red','green','blue']):
            path=self.base/f'ISOLATED_SCENE_{i}.png'; Image.new('RGB',(400,300),color).save(path)
            assets.append(self.lib.import_file(path,category='场景')[0])
        for i in range(2):
            self.service.select({'job_id':job['id'],'index':i,'asset_id':assets[i]['id']})
            # ISOLATED TEST FIXTURE: never a production approval.
            self.service.confirm({'job_id':job['id'],'index':i,'confirm':True})
        job=self.lib.get(job['id'],'jobs'); request=self.service.request(job,2)
        self.assertEqual([r['id'] for r in request['reference_records']],[assets[1]['id']])
        self.assertNotIn(assets[0]['id'],[r['id'] for r in request['reference_records']])
        self.service.clear_references({'job_id':job['id'],'index':2})
        request=self.service.request(self.lib.get(job['id'],'jobs'),2)
        self.assertEqual([r['id'] for r in request['reference_records']],[assets[1]['id']])
        self.service.reopen({'job_id':job['id'],'index':0})
        job=self.lib.get(job['id'],'jobs')
        self.assertTrue(all(step['status']=='waiting' for step in job['steps']))
        with self.assertRaisesRegex(ValueError,'确认前置'): self.service.request(job,2)

    def test_zero_asset_character_retains_story_and_refuses_image(self):
        from core.asset_refs import load_asset_list, require_images
        data = self.person('无需独立图')
        self.assertEqual(data['rows'], [])
        self.service.export({'project_id':self.p['id'], 'confirm':True})
        items = load_asset_list(Path(self.p['path']))['items']
        self.assertEqual(items[0]['state'], 'no_image')
        ok, problems = require_images(Path(self.p['path']), ['AST-PERSON@master'])
        self.assertFalse(ok)
        self.assertIn('no_image', ' '.join(problems))

    def test_scene_two_views_preserve_actual_dependencies(self):
        result = plan_result()
        row = result['rows'][0]
        row['outputs'] = row['outputs'][:2]
        row['outputs'][1].update(view='反向图', dependencies=['view0'])
        row['rationale'] = '母图与反向图覆盖全部可见空间，门口与侧墙留有机位余量'
        self.service.apply_plan(self.p, self.p['rows'], result, {'fixture':True})
        j = self.lib.get(self.service.get(self.p['id'])['rows'][0]['job_id'], 'jobs')
        self.assertEqual(len(j['steps']), 2)
        self.assertEqual(j['steps'][1]['dependencies'], ['view0'])

    def test_category_guide_has_one_full_body(self):
        skill = self.service.skill
        package = skill.build_task_package({'kind':'物品', 'mode':'asset-library'})
        guide = 'skills/asset-image-assistant/references/04-prop-product-master-guide.md'
        body = package['rules'][guide]
        serialized = json.dumps(package, ensure_ascii=False)
        self.assertEqual(serialized.count(json.dumps(body, ensure_ascii=False)), 1)
        template = package['production_item']['template']
        self.assertEqual(template['content_ref'], 'rules.' + guide)

    def test_phone_effect_does_not_become_face_mask(self):
        n = {'revision':'R01', 'story_paragraphs':[{'paragraph_id':'STP-1','text':'小林拿手机打电话。'}],
             'asset_requirements':[{'asset_id':'AST-PHONE','category':'REAL_PRODUCT','name':'测试手机','reference_paths':[]},
                                   {'asset_id':'AST-PERSON','category':'CHARACTER','name':'小林'}]}
        inventory = [{'product_id':'phone','product_name':'测试手机','aliases':[], 'media_type':'video'}]
        matched = {'references':[], 'effects':[{'product_ast_id':'AST-PHONE','character_ast_id':'AST-PERSON',
            'paragraph_ids':['STP-1'], 'evidence':'小林拿手机打电话。', 'wardrobe_state':'', 'state':'打电话',
            'reference_need':'手机贴耳时的握持方式', 'reference_reason':'核对产品与手部关系',
            'usage_kind':'actual', 'needs_reference':True}]}
        saved = usage.persist_matches(SimpleNamespace(), {'narrative':n, 'source':{'revision':'R01'}}, inventory, matched, 'fixture')
        e = saved['effects'][0]
        self.assertEqual(e['reference_need'], '手机贴耳时的握持方式')
        self.assertNotIn('FACE', e['request_id'])
        self.assertIsNone(e['generation_route'])


    def test_product_specific_mask_display_and_fantasy_are_separate(self):
        n={'revision':'R01','story_paragraphs':[{'paragraph_id':'STP-1','text':'小林将面膜敷在脸上。'}],
           'asset_requirements':[{'asset_id':'AST-MASK','category':'REAL_PRODUCT','name':'测试面膜','reference_paths':[]},
                                 {'asset_id':'AST-PERSON','category':'CHARACTER','name':'小林'}]}
        inventory=[{'product_id':'mask','product_name':'测试面膜','aliases':[], 'media_type':'video'}]
        project={'narrative':n,'source':{'revision':'R01'}}
        e={'product_ast_id':'AST-MASK','character_ast_id':'AST-PERSON','paragraph_ids':['STP-1'],
           'evidence':'小林将面膜敷在脸上。','wardrobe_state':'','state':'敷脸','usage_kind':'actual',
           'needs_reference':True,'reference_need':'人物真实敷面膜在脸上的可见效果参考','reference_reason':'当前产品明确敷脸需求，现有图未覆盖'}
        saved=usage.persist_matches(SimpleNamespace(),project,inventory,{'references':[],'effects':[e]},'fixture')
        self.assertEqual(saved['effects'][0]['reference_need'],e['reference_need'])
        self.assertEqual(saved['effects'][0]['status'],'awaiting_reference_review')
        n['story_paragraphs'][0]['text']='桌上展示面膜包装。'
        saved=usage.persist_matches(SimpleNamespace(),project,inventory,{'references':[],'effects':[]},'fixture')
        self.assertEqual(saved['effects'],[])
        n['story_paragraphs'][0]['text']='小林幻想在面膜温泉中游泳。'
        e.update(evidence=n['story_paragraphs'][0]['text'],state='幻想温泉',usage_kind='fantasy',
                 needs_reference=False,reference_need='',reference_reason='幻想成片由分镜承载，当前未要求独立真实使用图')
        saved=usage.persist_matches(SimpleNamespace(),project,inventory,{'references':[],'effects':[e]},'fixture')
        self.assertEqual(saved['effects'][0]['status'],'no_independent_reference')
        self.assertEqual(saved['effects'][0]['reference_need'],'')
        self.assertIsNone(saved['effects'][0]['generation_route'])


if __name__ == '__main__': unittest.main()
