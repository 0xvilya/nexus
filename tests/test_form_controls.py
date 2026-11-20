import sys,tempfile,unittest,os,json
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.actions import native_model_controls,plan_schema,ActionEngine
from rainforge_worker.browser import ManagedBrowser

class ControlTests(unittest.TestCase):
 def test_main_model_planner_receives_drag_refs(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();worker.runtime.status.return_value={'state':'ready'};worker.browser.target='prepared'
   worker.browser.observed={'context':1}
   worker.browser.observe.return_value={'url':'https://example.com','text':'Cards','elements':[{'ref':'e0','label':'Card','can_click':False,'can_type':False,'can_drag':True,'can_drop':False,'fingerprint':'one'}, {'ref':'e1','label':'Board','can_click':False,'can_type':False,'can_drag':False,'can_drop':True,'fingerprint':'two'}]}
   engine=ActionEngine(worker,Path(tmp));engine.route_web=True
   with patch.object(engine,'generate',return_value='{"actions":[{"tool":"browser_drag","source":"e0","target":"e1"}]}') as generate:
    result=engine.model_plan('Перетащи Card на Board на сайте',allow_interaction=True,bound_browser=True)
   self.assertEqual(result['actions'][0]['tool'],'browser_drag')
   self.assertIn('browser_drag',str(generate.call_args.kwargs['schema']))
   engine.authorize(result['actions'][0],'Перетащи Card на Board на сайте',None,True,True)
   with self.assertRaises(ValueError):engine.authorize(result['actions'][0],'прочитай сайт',None,True,True)
 def test_main_planner_drag_schema_uses_only_observed_targets(self):
  from rainforge_worker.computer_prompt import check_plan_scope
  selectors={'browser_drag_source':['e0'],'browser_drag_target':['e1']}
  schema=plan_schema({'browser_drag'},selectors=selectors)
  check_plan_scope({'tool':'browser_drag','source':'e0','target':'e1'},schema)
  for action in ({'tool':'browser_drag','source':'e9','target':'e1'},{'tool':'browser_drag','source':'e0','target':'e9'}):
   with self.assertRaises(ValueError):check_plan_scope(action,schema)
  self.assertEqual(plan_schema({'browser_drag'},selectors={'browser_drag_source':[],'browser_drag_target':['e1']})['properties']['actions']['items']['oneOf'],[])
 def test_compact_select_keeps_relevant_enabled_option(self):
  from rainforge_worker.computer_prompt import browser_controls
  options=[{'label':'Paris','value':'par','disabled':True}, {'label':'Paris Centre','value':'paris','disabled':False}]+[{'label':'Other '+('x'*110)+str(i),'value':str(i)} for i in range(30)]
  elements=[{'ref':'e0','label':'Destination','tag':'select','can_click':True,'can_type':True,'options':options}]
  selected=browser_controls(elements,'Choose Paris',max_bytes=450)
  self.assertEqual(selected[0]['ref'],'e0');self.assertEqual(selected[0]['options'][0]['value'],'paris')
  self.assertLessEqual(len(json.dumps(selected,ensure_ascii=False).encode()),450)
  self.assertEqual(len(options),32)
 def test_incomplete_dropdown_data_does_not_crash_ranking(self):
  from rainforge_worker.computer_prompt import browser_controls
  controls=browser_controls([{'ref':'e0','label':'City','can_type':True,'options':[{'value':'a'}, {'label':'Paris'}, None]}],'Paris')
  self.assertEqual(controls[0]['options'][0]['label'],'Paris')
 def test_reject_missing_or_extra_plan_fields(self):
  from rainforge_worker.computer_prompt import check_plan_scope
  schema=plan_schema({'browser_type'},selectors={'browser_type':['e0']})
  for action in (None, {}, {'tool':'browser_type'}, {'tool':'browser_type','selector':'e0','text':'x','extra':1}):
   with self.assertRaises(ValueError):check_plan_scope(action,schema)
  check_plan_scope({'tool':'browser_type','selector':'e0','text':'x'},schema)
 def test_option_changes_invalidate_browser_state(self):
  from rainforge_worker.browser_agent import state_signature,element_key
  e={'tag':'SELECT','label':'City','type':'select-one','value':'a','can_click':True,'can_type':True,'ref':'e0','options':[{'label':'A','value':'a'}]}
  state={'url':'https://example.com','text':'','elements':[e]}
  before=state_signature(state);e['options'][0]['label']='B'
  self.assertNotEqual(before,state_signature(state));e['fingerprint']={'unexpected':'data'}
  self.assertEqual(element_key(e),'e0')
 def test_expanded_native_tools_remain_within_schema_budget(self):
  from rainforge_worker.desktop import NATIVE_OPERATIONS
  from rainforge_worker.actions import DESKTOP_ACTIONS
  snapshot={'elements':[{'id':'7.'+str(i),'name':('Long control label '+str(i))*5,**{cap:True for cap in NATIVE_OPERATIONS.values()}} for i in range(300)]}
  controls=native_model_controls(snapshot)
  schema=plan_schema(set(DESKTOP_ACTIONS)|{'finish'},selectors={tool:[e['name'] for e in controls if e[cap]] for tool,op in DESKTOP_ACTIONS.items() for cap in [NATIVE_OPERATIONS[op]]})
  self.assertLess(len(json.dumps(schema)),16000);self.assertGreater(len(controls),0)
 def test_large_native_labels_do_not_expand_model_schema(self):
  snapshot={'elements':[{'id':'7.'+str(i),'name':('Сообщение '*1000)+str(i),'invoke':True,'type_text':True} for i in range(300)]}
  controls=native_model_controls(snapshot);self.assertEqual(len(controls),24)
  schema=plan_schema({'desktop_read','desktop_invoke','desktop_type','finish'},selectors={'desktop_invoke':[e['name'] for e in controls],'desktop_type':[e['name'] for e in controls]})
  self.assertLess(len(json.dumps(schema)),16000);self.assertTrue(all(e['name'].startswith('7.') for e in controls))
 def test_duplicate_names_use_actual_runtime_ids(self):
  snapshot={'elements':[{'id':'7.1','name':'Open','invoke':True},{'id':'7.2','name':'Open','invoke':True},{'id':'7.3','name':'Search','type_text':True}]}
  self.assertEqual([e['name'] for e in native_model_controls(snapshot)],['7.1','7.2','Search'])
 def test_real_model_plan_schema_with_huge_snapshot(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();worker.runtime.status.return_value={'state':'ready'};worker.browser.target=None
   engine=ActionEngine(worker,Path(tmp));engine.desktop_intent=True
   snapshot={'elements':[{'id':'7.'+str(i),'name':'Приватное длинное содержимое '*500,'invoke':True} for i in range(300)]}
   with patch.object(engine,'generate',return_value='{"actions":[{"tool":"desktop_read"}]}') as generate:
    engine.model_plan('Прочитай выбранное окно',snapshot,allow_interaction=True)
   self.assertLess(len(json.dumps(generate.call_args.kwargs['schema'])),16000)
   self.assertLess(len(generate.call_args.args[0]),12000)
 @unittest.skipUnless(os.environ.get('RF_BROWSER_TEST')=='1','Native managed browser opt in')
 def test_real_dropdown_date_checkbox_and_submit_handoff(self):
  with tempfile.TemporaryDirectory(ignore_cleanup_errors=True) as tmp:
   browser=ManagedBrowser(Path(tmp))
   try:
    html='<form method=post><select aria-label="Destination"><option value="lon">London</option><option value="par">Paris</option><option disabled value="bad">Blocked</option></select><input type=date aria-label="Date"><input type=checkbox aria-label="Newsletter"><button type=submit>Buy ticket</button></form>'
    browser.evaluate('document.body.innerHTML='+json.dumps(html))
    def act(label,text=None):
     state=browser.observe();element=next(e for e in state['elements'] if e['label']==label)
     return browser.act_observed(state['token'],element['ref'],'click' if text is None else 'type',text or '')
    self.assertTrue(act('Destination','Paris')['acted']);self.assertEqual(browser.evaluate('document.querySelector("select").value'),'par')
    self.assertFalse(act('Destination','Blocked')['acted']);self.assertEqual(browser.evaluate('document.querySelector("select").value'),'par')
    self.assertTrue(act('Date','2026-12-20')['acted']);self.assertFalse(act('Date','tomorrow')['acted'])
    self.assertEqual(browser.evaluate('document.querySelector("input[type=date]").value'),'2026-12-20')
    self.assertTrue(act('Newsletter')['acted']);self.assertTrue(browser.evaluate('document.querySelector("input[type=checkbox]").checked'))
    self.assertEqual(act('Buy ticket')['reason'],'manual_confirmation_required')
    # Options changing between observation and action must invalidate the old ref.
    state=browser.observe();element=state['elements'][0];browser.evaluate('document.querySelector("select").options[0].value="changed"')
    self.assertEqual(browser.act_observed(state['token'],element['ref'],'type','London')['reason'],'stale_observation')
   finally:browser.close()

if __name__=='__main__':unittest.main()
