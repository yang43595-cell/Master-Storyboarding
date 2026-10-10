const {test}=require('node:test');
const assert=require('node:assert/strict');
const fs=require('node:fs');
const vm=require('node:vm');
const path=require('node:path');
const source=fs.readFileSync(path.join(__dirname,'../visual_asset_ui/characters.js'),'utf8').split('function requirementRow(')[0];
function setup(){
 const elements=new Map(),calls=[];
 const ctx=vm.createContext({
  document:{addEventListener(){}},esc:s=>String(s).replaceAll('<','&lt;'),
  projectData:{rows:[{requirement:{asset_id:'AST-ONE',name:'酒瓶'}}],narrative:{story_paragraphs:[{paragraph_id:'STP-001',text:'入口，人物手提礼物。'}],_asset_choices:{excluded:{'AST-TWO':{name:'美容卡'}}}}},
  modal:(title,html)=>calls.push({title,html}),$:s=>elements.get(s),
  guarded:fn=>fn(),projectApi:async(action,payload)=>{calls.push({action,payload});return ctx.projectData;},
  projectSelection:{clear(){}},refresh:async()=>{},projectRender(){},toast(){}
 });
 vm.runInContext(source,ctx);return {ctx,elements,calls};
}
test('plus entry offers addition and recovery without a delete list',()=>{
 const {ctx,calls}=setup();vm.runInContext('assetChoicesDialog()',ctx);
 assert.doesNotMatch(calls[0].html,/data-asset-exclude/);
 assert.match(calls[0].html,/data-asset-add/);
 assert.match(calls[0].html,/data-asset-restore="AST-TWO"/);
 assert.match(calls[0].html,/本项目不需要/);
});
test('addition submits user checked appearance and selected story ids',async()=>{
 const {ctx,elements,calls}=setup();
 const form={elements:{name:{value:'雨伞'},category:{value:'PROP'},source:{selectedOptions:[{value:'STP-001'}]},appearance:{value:'入口手提雨伞'},wardrobe_states:{value:''},character_type:{value:'HUMAN'},checked:{checked:true}}};
 elements.set('#asset-add-form',form);elements.set('#asset-source-preview',{});
 vm.runInContext('assetAddDialog()',ctx);form.elements.source.onchange();
 assert.match(elements.get('#asset-source-preview').textContent,/入口，人物手提礼物/);
 await form.onsubmit({preventDefault(){},submitter:{}});
 const request=calls.find(c=>c.action==='asset-choice');
 assert.equal(request.payload.operation,'add');
 assert.equal(request.payload.source_ids[0],'STP-001');
 assert.equal(request.payload.location_confirmed,true);
 assert.equal(request.payload.appearance,'入口手提雨伞');
});

test('fantasy companion is presented under pets and bypasses human display label',()=>{
 const {ctx,elements,calls}=setup();
 assert.equal(vm.runInContext("requirementCategory({category:'CHARACTER',character_type:'PET'})",ctx),'宠物');
 assert.equal(vm.runInContext("requirementCategory({category:'CHARACTER',character_type:'ANIMAL'})",ctx),'宠物');
 elements.set('#asset-add-form',{elements:{source:{}}});
 vm.runInContext('assetAddDialog()',ctx);
 assert.match(calls[0].html, /value="PET">宠物／幻想伙伴/);
});
