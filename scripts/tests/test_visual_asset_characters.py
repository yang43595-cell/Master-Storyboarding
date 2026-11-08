"""Character routes: reusable final assets and shared-head outfit dependencies."""
import copy
import json
import shutil
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch
from PIL import Image
from test_visual_asset_projects import fixture, update_narrative_checksum, synthetic_vault
from visual_asset_characters import character_route, upstream_outfits, prepare_character
import visual_asset_library as m

class CharacterRoutes(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.base = Path(self.temp.name)
        self.vault_env = patch.object(m.asset_skill, 'resolve_knowledge_root',
                                      return_value=synthetic_vault(self.base / 'synthetic-vault'))
        self.vault_env.start()
        self.addCleanup(self.vault_env.stop)
        self.lib, self.project, _ = fixture(self.base)
        path = Path(self.project['path']) / 'narrative.json'
        n = json.loads(path.read_text(encoding='utf8'))
        req = copy.deepcopy(n['asset_requirements'][0])
        req.update(asset_id='AST-PERSON', category='CHARACTER', entity_id='CHR-PERSON', name='人物甲', identity_constraints=['日常装与出差装，共用人物身份'], required_states=['日常', '出差'])
        n['asset_requirements'] = [req]
        path.write_text(json.dumps(n, ensure_ascii=False), encoding='utf8')
        update_narrative_checksum(path.parent)
        self.lib.projects.sync(self.project['id'])
        self.assets = {}
        for kind, size in [('大头照', (300,400)), ('服装三视图',(160,90)), ('人物1x4',(160,90))]:
            p=self.base/(kind+'.png');Image.new('RGB',size,'blue').save(p)
            self.assets[kind] = self.lib.import_file(p, category=kind)[0]
    def tearDown(self):
        self.temp.cleanup()

    def test_animal_body_route_reuses_and_exports_without_human_clothing(self):
        path = Path(self.project['path']) / 'narrative.json'
        n = json.loads(path.read_text(encoding='utf8'))
        req = n['asset_requirements'][0]
        req.update(character_type='ANIMAL', name='猫咪', identity_constraints=['公主的猫咪'],
                   required_states=['追逐', '奔跑', '床边休息'])
        path.write_text(json.dumps(n, ensure_ascii=False), encoding='utf8')
        update_narrative_checksum(path.parent)
        service = self.lib.projects
        service.sync(self.project['id'])
        row = service.detail(self.project['id'])['rows'][0]
        self.assertFalse(row['uses_wardrobe'])
        self.assertNotIn('outfit_error', row)
        with self.assertRaises(ValueError):
            prepare_character(service, {'project_id': self.project['id'], 'ast_id': 'AST-PERSON'})
        data = service.prepare_reuse({'project_id': self.project['id'], 'ast_id': 'AST-PERSON'})
        job = data['rows'][0]['job']
        self.assertEqual([s['kind'] for s in job['steps']], ['角色外观'])
        self.assertEqual(job['steps'][0]['dependencies'], [])
        with self.assertRaises(ValueError):
            service.select({'job_id': job['id'], 'index': 0, 'asset_id': self.assets['人物1x4']['id']})
        service.production_route({'project_id': self.project['id'], 'ast_id': 'AST-PERSON', 'route': 'build'})
        request = service.request(self.lib.get(job['id'], 'jobs'), 0)
        self.assertEqual(request['kind'], '角色外观')
        self.assertIsNone(request['character_outfit'])
        self.assertEqual(service.skill.method_request(request)['asset_method_scopes'], {'body': True})
        refs = service.skill.local_references('角色外观', {'mode': 'project', 'rows': [row]})
        self.assertTrue(any('02a-body-character' in r for r in refs))
        self.assertFalse(any('02-character-master' in r for r in refs))
        image = self.base / 'body.png'; Image.new('RGB', (160, 90), 'blue').save(image)
        animal, _ = self.lib.import_file(image, category='角色外观')
        service.select({'job_id': job['id'], 'index': 0, 'asset_id': animal['id']})
        service.confirm({'job_id': job['id'], 'index': 0, 'asset_id': animal['id'], 'confirm': True})
        animal = self.lib.get(animal['id'])
        backup = self.base / 'body-backup.png'; shutil.copy2(animal['original'], backup)
        animal['backup'] = {'status': 'saved', 'path': str(backup)}; self.lib.save(animal)
        out = service.export({'project_id': self.project['id'], 'confirm': True})
        record = json.loads(Path(out['manifest']).read_text(encoding='utf8'))['assets'][0]
        self.assertEqual(record['character_type'], 'ANIMAL')
        self.assertEqual(len(record['confirmed_outputs']), 1)

    def test_explicit_animal_correction_preserves_confirmed_story_and_reaches_downstream(self):
        from core.asset_refs import load_asset_list, scope_assets
        from visual_asset_choices import change
        path = Path(self.project['path']) / 'narrative.json'
        before = path.read_bytes()
        data = change(self.lib.projects, {'project_id': self.project['id'], 'operation': 'character-type',
            'ast_id': 'AST-PERSON', 'character_type': 'ANIMAL', 'evidence': '用户确认此角色是动物', 'confirm': True})
        self.assertEqual(path.read_bytes(), before)
        self.assertFalse(data['rows'][0]['uses_wardrobe'])
        item = load_asset_list(Path(self.project['path']))['items'][0]
        self.assertEqual(item['character_type'], 'ANIMAL')
        scoped, _ = scope_assets(Path(self.project['path']), ['AST-PERSON'])
        self.assertFalse(scoped[0]['uses_wardrobe'])
    def test_explicit_fairy_pet_route_uses_three_views_and_keeps_story(self):
        from core.asset_refs import load_asset_list
        from visual_asset_choices import change
        from core.character_assets import production_kinds
        story = Path(self.project['path']) / 'narrative.json'
        before = story.read_bytes()
        result = change(self.lib.projects, {'project_id': self.project['id'], 'operation': 'character-type',
            'ast_id': 'AST-PERSON', 'character_type': 'PET', 'evidence': '用户指定小精灵放宠物层', 'confirm': True})
        row = result['rows'][0]
        self.assertFalse(row['uses_wardrobe'])
        self.assertEqual(production_kinds(row['requirement']), ['角色外观'])
        self.assertEqual(story.read_bytes(), before)
        item = load_asset_list(Path(self.project['path']))['items'][0]
        self.assertEqual(item['character_type'], 'PET')
        recipe = self.lib.projects.skill.recipe('角色外观')
        self.assertEqual(recipe['category'], '宠物')
        self.assertIn('正面、左侧、背面', recipe['description'])
        planned = {'rows': [{'ast_id': 'AST-PERSON', 'rationale': '宠物三视图', 'blockers': [], 'outputs': [{
            'key': 'pet_views', 'kind': '角色外观', 'view': '旧单张标签', 'state': '外观', 'ratio': '3:4',
            'purpose': '完整造型', 'rationale': '用户要求', 'dependencies': [], 'must_not_inherit': []}]}]}
        self.lib.projects.apply_plan(result, [row], planned, {})
        job = self.lib.get(self.lib.projects.get(self.project['id'])['rows'][0]['job_id'], 'jobs')
        self.assertEqual(len(job['steps']), 1)
        self.assertEqual(job['steps'][0]['ratio'], '16:9')
        self.assertIn('正面、左侧、背面', job['steps'][0]['view'])
        self.assertNotIn('character_workflow', job)

    def route(self, route, **kw):
        data=character_route(self.lib.projects,{'project_id':self.project['id'],'ast_id':'AST-PERSON','route':route,**kw})
        return data['rows'][0]['job']
    def confirm(self, job, key, category):
        i=next(i for i,s in enumerate(job['steps']) if s['key']==key)
        a=self.assets[category]
        self.lib.projects.select({'job_id':job['id'],'index':i,'asset_id':a['id']})
        return self.lib.projects.confirm({'job_id':job['id'],'index':i,'asset_id':a['id'],'confirm':True})
    def test_direct_reuse_does_not_require_head_and_cannot_generate(self):
        j=self.route('reuse')
        self.assertEqual(len(j['steps']),1)
        self.assertEqual(j['steps'][0]['dependencies'],[])
        with self.assertRaises(ValueError):self.lib.projects.request(j,0)
        with self.assertRaises(ValueError):self.lib.projects.select({'job_id':j['id'],'index':0,'asset_id':self.assets['大头照']['id']})
        self.confirm(j,j['steps'][0]['key'],'人物1x4')
        a=self.lib.get(self.assets['人物1x4']['id']);backup=self.base/'backed.png';shutil.copy2(a['original'],backup)
        a['backup']={'status':'saved','path':str(backup)};self.lib.save(a)
        result=self.lib.projects.export({'project_id':self.project['id'],'confirm':True})
        self.assertEqual(result['count'],1)
    def test_shared_head_two_outfits_and_local_invalidation(self):
        j=self.route('build',name='日常装',evidence='日常装')
        first=j['character_workflow']['outfits'][0]['id']
        j=self.route('build',name='出差装',evidence='出差装')
        second=j['character_workflow']['outfits'][1]['id']
        self.assertEqual(sum(s['kind']=='大头照' for s in j['steps']),1)
        self.assertEqual(len(j['steps']),5)
        with self.assertRaises(ValueError):self.confirm(j,first+'_final','人物1x4')
        self.confirm(j,'shared_head','大头照')
        self.confirm(j,first+'_wardrobe','服装三视图');self.confirm(j,second+'_wardrobe','服装三视图')
        j=self.lib.get(j['id'],'jobs')
        i=next(i for i,s in enumerate(j['steps']) if s['key']==second+'_final')
        request = self.lib.projects.request(j,i)
        self.assertEqual([r['category'] for r in request['reference_records']], ['服装三视图', '大头照'])
        self.assertEqual(j['steps'][i]['status'],'waiting')
        self.confirm(j,first+'_final','人物1x4');self.confirm(j,second+'_final','人物1x4')
        j=self.lib.get(j['id'],'jobs')
        receipt=j['steps'][i]['project_confirmation']
        self.assertEqual([r['output_key'] for r in receipt['dependency_confirmations']],['shared_head',second+'_wardrobe'])
        self.assertTrue(all(r['confirmation_id'] for r in receipt['dependency_confirmations']))
        i=next(i for i,s in enumerate(j['steps']) if s['key']==first+'_wardrobe')
        self.lib.projects.reopen({'job_id':j['id'],'index':i})
        j=self.lib.get(j['id'],'jobs')
        self.assertEqual(next(s for s in j['steps'] if s['key']==second+'_final')['status'],'confirmed')
        self.lib.projects.reopen({'job_id':j['id'],'index':next(i for i,s in enumerate(j['steps']) if s['key']=='shared_head')})
        j=self.lib.get(j['id'],'jobs')
        self.assertTrue(all(s['status']=='waiting' for s in j['steps'] if s['kind']=='人物1x4'))
        self.assertEqual(next(s for s in j['steps'] if s['key']==second+'_wardrobe')['status'],'confirmed')
    def test_route_changes_keep_indices_history_and_reject_unregistered_outfits(self):
        j=self.route('reuse');v=j['character_workflow']['outfits'][0]['id'];finalkey=j['steps'][0]['key']
        self.confirm(j,finalkey,'人物1x4')
        j=self.route('build',outfit_id=v)
        self.assertEqual(j['steps'][0]['key'],finalkey)
        self.assertTrue(j['steps'][0]['history'][0]['project_confirmation'])
        j=self.route('reuse',outfit_id=v)
        self.assertEqual(sum(s.get('required',True) for s in j['steps']),1)
        before=copy.deepcopy(j)
        with self.assertRaises(ValueError):self.route('build',name='太空服',evidence='未登记的太空服')
        self.assertEqual(self.lib.get(j['id'],'jobs'),before)

    def test_new_character_export_uses_final_as_primary(self):
        j=self.route('build');v=j['character_workflow']['outfits'][0]['id']
        self.confirm(j,'shared_head','大头照')
        self.confirm(j,v+'_wardrobe','服装三视图')
        self.confirm(j,v+'_final','人物1x4')
        j=self.lib.get(j['id'],'jobs')
        self.assertTrue(all(s['status']=='confirmed' for s in j['steps']))
        for step in j['steps']:
            a=self.lib.get(step['selected']);backup=self.base/(a['id']+'-backup.png');shutil.copy2(a['original'],backup)
            a['backup']={'status':'saved','path':str(backup)};self.lib.save(a)
        result=self.lib.projects.export({'project_id':self.project['id'],'confirm':True})
        out=json.loads(Path(result['manifest']).read_text(encoding='utf8'))['assets'][0]
        final=next(o for o in out['confirmed_outputs'] if o['output_id']==v+'_final')
        self.assertEqual(out['confirmed_asset']['candidate_id'],final['candidate_id'])
        self.assertEqual(len(out['confirmed_outputs']),3)

    def test_ai_board_waits_for_both_inputs_and_requires_explicit_generation_authorization(self):
        originals=[Path(a['original']).read_bytes() for a in [self.assets['大头照'],self.assets['服装三视图']]]
        j=self.route('build');v=j['character_workflow']['outfits'][0]['id']
        index=next(i for i,s in enumerate(j['steps']) if s['key']==v+'_final')
        with self.assertRaisesRegex(ValueError, '前置制作项'):
            self.lib.projects.request(j,index)
        self.confirm(j,v+'_wardrobe','服装三视图')
        self.assertEqual(next(s for s in self.lib.get(j['id'],'jobs')['steps'] if s['key']==v+'_final')['status'],'waiting')
        before = len(self.lib.items())
        with patch.object(self.lib, 'submit_imagegen') as generate, patch.object(self.lib, 'submit') as dreamina:
            self.confirm(j,'shared_head','大头照')
        generate.assert_not_called();dreamina.assert_not_called()
        self.assertEqual(len(self.lib.items()), before)
        j=self.lib.get(j['id'],'jobs');final=next(s for s in j['steps'] if s['key']==v+'_final')
        self.assertEqual(final['status'],'waiting');self.assertTrue(final['generation_enabled'])
        self.assertEqual(final['outputs'], []);self.assertIsNone(final['project_confirmation'])
        request = self.lib.projects.request(j,index)
        self.assertEqual([r['category'] for r in request['reference_records']], ['服装三视图','大头照'])
        self.assertIn('图1：已确认服装三视图，服装及无头身体来源', request['prompt'])
        self.assertIn('图2：已确认大头照，唯一面部身份来源', request['prompt'])
        self.assertEqual(originals,[Path(a['original']).read_bytes() for a in [self.assets['大头照'],self.assets['服装三视图']]])
        head_index=next(i for i,s in enumerate(j['steps']) if s['key']=='shared_head')
        again=self.lib.projects.confirm({'job_id':j['id'],'index':head_index,'confirm':True})
        self.assertFalse(next(s for s in again['rows'][0]['job']['steps'] if s['key']==v+'_final').get('selected'))
        tasks = [{'job_id':j['id'],'index':index}]
        with patch('visual_asset_projects.threading.Thread.start') as start:
            self.lib.projects.batch({'project_id':self.project['id'],'mode':'prepare','tasks':tasks})
            entry = self.lib.projects.get(self.project['id'])['batch']['entries'][0]
            self.assertEqual(entry['operation'], 'write')
            self.assertEqual(entry['request']['kind'], '人物1x4')
            start.assert_called_once()
        # Queue preparation alone is not authorization to submit image generation.
        self.lib.projects.batch_running = False
        j=self.lib.get(j['id'],'jobs');final=j['steps'][index]
        final.update(prompt='四格人物资产候选', audit={'status':'passed','prompt':'四格人物资产候选',
            'skill_execution':{'version':self.lib.projects.skill.profile()['version'],'mode':'project'}})
        self.lib.save(j,'jobs')
        tasks[0]['prompt']=final['prompt']
        with patch('visual_asset_projects.threading.Thread.start') as start:
            with self.assertRaisesRegex(ValueError, '额度消耗'):
                self.lib.projects.batch({'project_id':self.project['id'],'mode':'generate','tasks':tasks})
            start.assert_not_called()
            self.lib.projects.batch({'project_id':self.project['id'],'mode':'generate','tasks':tasks,'authorize':True})
            self.assertEqual(self.lib.projects.get(self.project['id'])['batch']['mode'],'generate')
            start.assert_called_once()

    def test_legacy_pending_composition_is_unblocked_without_images_or_confirmations(self):
        j=self.route('build');v=j['character_workflow']['outfits'][0]['id']
        self.confirm(j,'shared_head','大头照')
        self.confirm(j,v+'_wardrobe','服装三视图')
        j=self.lib.get(j['id'],'jobs');final=next(s for s in j['steps'] if s['key']==v+'_final')
        sources=copy.deepcopy([s['project_confirmation'] for s in j['steps'] if s['kind']!='人物1x4'])
        final.update(generation_enabled=False, composition_error=True, message='人物1×4自动合成未完成：disk unavailable')
        self.lib.save(j,'jobs');before=self.lib.items()
        row=self.lib.projects.detail(self.project['id'])['rows'][0]
        j=row['job'];final=next(s for s in j['steps'] if s['key']==v+'_final')
        self.assertTrue(final['generation_enabled']);self.assertNotIn('composition_error',final)
        self.assertNotIn('message',final);self.assertEqual(final['status'],'waiting')
        self.assertEqual(sources,[s['project_confirmation'] for s in j['steps'] if s['kind']!='人物1x4'])
        self.assertFalse(final['project_confirmation']);self.assertEqual(final['outputs'],[])
        final['generation_enabled']=False
        self.lib.save(j,'jobs')
        self.lib.projects.request(j,j['steps'].index(final))
        self.assertTrue(self.lib.get(j['id'],'jobs')['steps'][j['steps'].index(final)]['generation_enabled'])
        with self.assertRaisesRegex(ValueError, '已改为AI生成'):
            self.lib.projects.compose_character({'job_id':j['id'],'index':j['steps'].index(final)})
        self.assertEqual(self.lib.items(),before)

    def test_confirmed_legacy_composite_and_receipt_remain_unchanged(self):
        j=self.route('build');v=j['character_workflow']['outfits'][0]['id']
        self.confirm(j,'shared_head','大头照');self.confirm(j,v+'_wardrobe','服装三视图')
        self.confirm(j,v+'_final','人物1x4')
        j=self.lib.get(j['id'],'jobs');final=next(s for s in j['steps'] if s['key']==v+'_final')
        final['generation_enabled']=False
        final['project_confirmation']['method']='已确认大头照与服装三视图自动拼版'
        self.lib.save(j,'jobs')
        asset=self.lib.get(final['selected']);asset['provenance']['kind']='character_composite'
        asset['confirmation']=copy.deepcopy(final['project_confirmation']);self.lib.save(asset)
        original=Path(asset['original']).read_bytes()
        for _ in range(2):
            self.lib.projects.detail(self.project['id'])
            self.assertEqual(self.lib.get(j['id'],'jobs'),j)
            self.assertEqual(self.lib.get(asset['id']),asset)
        self.assertEqual(Path(asset['original']).read_bytes(),original)

    def test_empty_head_uses_requirement_and_wardrobe_inherits_confirmed_head(self):
        j=self.route('build',name='日常装',evidence='日常装')
        head=next(i for i,s in enumerate(j['steps']) if s['key']=='shared_head')
        request=self.lib.projects.request(j,head)
        self.assertEqual(request['reference_records'],[])
        self.assertTrue(request['allow_unreferenced_design'])
        self.assertEqual(request['requirement']['asset_id'],'AST-PERSON')
        self.assertIn('依据当前需求设计', request['prompt'])
        self.assertNotIn('从参考照片中提取', request['prompt'])
        self.assertEqual(self.lib.projects.skill.build_task_package(request)['production_item']['template']['content'], request['prompt'])
        self.confirm(j,'shared_head','大头照')
        j=self.lib.get(j['id'],'jobs')
        cloth=next(i for i,s in enumerate(j['steps']) if s['kind']=='服装')
        request=self.lib.projects.request(j,cloth)
        self.assertEqual(request['reference_records'][0]['id'],self.assets['大头照']['id'])
        self.assertTrue(request['allow_unreferenced_design'])
        self.assertIn('已确认大头照，唯一面部身份来源', request['prompt'])
        self.assertIn('不把身份参考中的服装当作本次服装设计', request['prompt'])

    def test_upstream_clothes_ignore_skin_and_preserve_unassigned_legacy_image(self):
        path=Path(self.project['path'])/'narrative.json'
        n=json.loads(path.read_text(encoding='utf8'))
        req=n['asset_requirements'][0]
        req['identity_constraints']=['燕麦针织裙(前)→冰蓝缎面裙(后)','皮肤暗沉→水光肌']
        path.write_text(json.dumps(n,ensure_ascii=False),encoding='utf8')
        update_narrative_checksum(path.parent)
        self.lib.projects.sync(self.project['id'])
        j=self.route('reuse')
        self.confirm(j,j['steps'][0]['key'],'人物1x4')
        old=self.lib.get(j['id'],'jobs')
        payload={'project_id':self.project['id'],'ast_id':'AST-PERSON'}
        result=prepare_character(self.lib.projects,payload)
        row=result['rows'][0];new=row['job']
        self.assertEqual(len(new['character_workflow']['outfits']),2)
        self.assertEqual(row['upstream_outfits'][1]['name'],'冰蓝缎面裙(后)')
        self.assertEqual(self.lib.get(old['id'],'jobs'),old)
        self.assertIn(self.assets['人物1x4']['id'],row['previous_character_images'])
        self.assertTrue(all(not s['project_confirmation'] for s in new['steps']))
        self.assertEqual(prepare_character(self.lib.projects,payload)['rows'][0]['job'],new)
        with self.assertRaises(ValueError):
            self.lib.projects.action({'action':'character-route',**payload,'name':'新衣服','route':'reuse'})
        v=new['character_workflow']['outfits'][1]
        result=self.lib.projects.action({'action':'character-route',**payload,'outfit_id':v['id'],'route':'build'})
        self.assertEqual(len(result['rows'][0]['job']['character_workflow']['outfits']),2)

    def test_single_outfit_components_and_missing_upstream(self):
        self.assertEqual(len(upstream_outfits({'identity_constraints':['藏青西装+浅灰衬衫','惊讶→高兴']})),1)
        with self.assertRaises(ValueError):upstream_outfits({'identity_constraints':['皮肤暗沉→水光肌']})

    def test_explicit_states_do_not_require_garment_design(self):
        req = {'identity_constraints': ['琳琳丈夫；第二次换装'],
               'wardrobe_states': [
                   {'name': '首次饭局', 'evidence': '首次饭局'},
                   {'name': '二次会面', 'evidence': '第二次换装'}]}
        self.assertEqual(upstream_outfits(req), req['wardrobe_states'])
        for bad in ([], [{'name': '首次饭局'}], req['wardrobe_states'][:1] * 2):
            with self.subTest(bad=bad), self.assertRaises(ValueError):
                upstream_outfits({**req, 'wardrobe_states': bad})

    def set_approved_character_output(self, kind, **details):
        path = Path(self.project['path']) / 'narrative.json'
        narrative = json.loads(path.read_text(encoding='utf8'))
        narrative.pop('events')
        narrative.pop('human_readable_path')
        narrative.update(schema_version='2.0', story_paragraphs=[
            {'paragraph_id': 'STP-01', 'text': '人物甲出现在日常场景及出差场景。'},
        ])
        requirement = narrative['asset_requirements'][0]
        requirement.pop('source_event_ids')
        requirement.update(character_output_kind=kind, source_paragraph_ids=['STP-01'],
                           image_decision='需要制作', image_reason='固定人物甲的造型', **details)
        path.write_text(json.dumps(narrative, ensure_ascii=False), encoding='utf8')
        update_narrative_checksum(path.parent)

    def test_approved_board_route_binds_exact_outfit_ids_and_rejects_single(self):
        service = self.lib.projects
        payload = {'project_id': self.project['id'], 'ast_id': 'AST-PERSON'}
        service.character_output_mode({**payload, 'mode': 'single'})
        old_job = service.prepare_reuse(payload)['rows'][0]['job']
        self.set_approved_character_output('人物1x4', wardrobe_states=[
            {'name': '日常装', 'evidence': '日常场景', 'output_id': 'HOME-FINAL'},
            {'name': '出差装', 'evidence': '出差场景', 'output_id': 'TRAVEL-FINAL'},
        ])

        row = service.sync(self.project['id'])['rows'][0]
        self.assertEqual(row['character_output_mode'], 'wardrobe')
        self.assertTrue(row['uses_wardrobe'])
        self.assertIsNone(row['job_id'])
        with self.assertRaisesRegex(ValueError, '旧版需求'):
            service.context(old_job, 0)
        with self.assertRaisesRegex(ValueError, '最终人物1×4'):
            service.character_output_mode({**payload, 'mode': 'single'})
        with self.assertRaisesRegex(ValueError, '人物卡片'):
            service.prepare_reuse(payload)

        job = prepare_character(service, payload)['rows'][0]['job']
        finals = [(step['state'], step['output_id'], step['kind']) for step in job['steps']]
        self.assertEqual(finals, [
            ('日常装', 'HOME-FINAL', '人物1x4'),
            ('出差装', 'TRAVEL-FINAL', '人物1x4'),
        ])
        with self.assertRaisesRegex(ValueError, '请选择人物1x4'):
            service.select({'job_id': job['id'], 'index': 0, 'asset_id': self.assets['大头照']['id']})

    def test_approved_single_route_uses_only_single_picture_jobs(self):
        service = self.lib.projects
        payload = {'project_id': self.project['id'], 'ast_id': 'AST-PERSON'}
        self.set_approved_character_output('人物单图')

        row = service.sync(self.project['id'])['rows'][0]
        self.assertEqual(row['character_output_mode'], 'single')
        self.assertFalse(row['uses_wardrobe'])
        with self.assertRaisesRegex(ValueError, '上游已确认使用人物单图'):
            service.character_output_mode({**payload, 'mode': 'wardrobe'})
        with self.assertRaisesRegex(ValueError, '只处理人物需求'):
            prepare_character(service, payload)
        job = service.prepare_reuse(payload)['rows'][0]['job']
        self.assertTrue(job['steps'])
        self.assertTrue(all(step['kind'] == '人物单图' for step in job['steps']))
        with self.assertRaisesRegex(ValueError, '请从人物单图分类'):
            service.select({'job_id': job['id'], 'index': 0, 'asset_id': self.assets['人物1x4']['id']})

    def test_explicit_states_pass_source_prepare_and_refresh(self):
        path = Path(self.project['path']) / 'narrative.json'
        n = json.loads(path.read_text(encoding='utf8'))
        n['schema_version'] = '2.0'
        n.pop('events')
        n['story_paragraphs'] = [{'paragraph_id': 'STP-001', 'text': '两次会面，第二次换装。'}]
        (path.parent / '叙事.md').write_text('## 故事正文\n### STP-001｜会面\n两次会面，第二次换装。\n', encoding='utf8')
        req = n['asset_requirements'][0]
        req.pop('source_event_ids')
        req['source_paragraph_ids'] = ['STP-001']
        req['identity_constraints'] = ['第二次换装']
        req['wardrobe_states'] = [
            {'name': '首次饭局', 'evidence': '两次会面'},
            {'name': '二次会面', 'evidence': '第二次换装'}]
        path.write_text(json.dumps(n, ensure_ascii=False), encoding='utf8')
        update_narrative_checksum(path.parent)
        self.lib.projects.sync(self.project['id'])
        payload = {'project_id': self.project['id'], 'ast_id': 'AST-PERSON'}
        row = prepare_character(self.lib.projects, payload)['rows'][0]
        self.assertEqual([x['name'] for x in row['upstream_outfits']], ['首次饭局', '二次会面'])
        self.assertEqual(len(row['job']['steps']), 2)
        self.assertEqual(prepare_character(self.lib.projects, payload)['rows'][0]['job'], row['job'])
        self.assertTrue(all(not s['project_confirmation'] for s in row['job']['steps']))
