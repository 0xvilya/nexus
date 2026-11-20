import sys,tempfile,unittest
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.actions import ActionEngine
from rainforge_worker.workspace import WorkspaceTools

class ActionLoopTests(unittest.TestCase):
 def test_prompt_retry_keeps_ranked_observation_and_rejects_unshown_ref(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker,engine=self.setup_worker(tmp)
   worker.runtime.status.return_value={'state':'ready'}
   worker.browser.target='owned'
   worker.browser.observed={'context':12}
   controls=[{'ref':f'e{i}','label':f'Other {i}','can_click':True,'can_type':False,'fingerprint':str(i)} for i in range(75)]
   controls[-1]['label']='Customer invoice'
   worker.browser.observe.return_value={'url':'https://example.org','text':'Read this page','elements':controls}
   outputs=['{"actions":[{"tool":"browser_click","selector":"e73"}]}','{"actions":[{"tool":"browser_click","selector":"e74"}]}']
   with patch.object(engine,'generate',side_effect=outputs) as generate:
    plan=engine.model_plan('Click Customer invoice on this page',bound_browser=True)
   self.assertEqual(plan['actions'][0]['selector'],'e74')
   first,retry=generate.call_args_list
   self.assertIn('Customer invoice',first.args[0])
   self.assertTrue(retry.args[0].endswith(first.args[0]))
   self.assertEqual(first.kwargs['schema'],retry.kwargs['schema'])
   self.assertNotIn('"e73"',first.args[0])

 def test_origin_change_stops_further_model_actions(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.browser.target='owned'
   def choose(*args,**kwargs):
    engine.model_browser={'url':'https://first.example','token':'selected','elements':[{'ref':'e0','fingerprint':'button'}]}
    return {'actions':[{'tool':'browser_click','selector':'e0'}]}
   w.browser.act_observed.return_value={'acted':True};w.browser.inspect.return_value={'url':'https://other.example','text':'Changed site'}
   with patch.object(engine,'model_plan',side_effect=choose) as planner:
    result=engine.execute('Click Continue and inspect its result',use_model=True,allow_interaction=True)
   self.assertEqual(result['state'],'awaiting_manual');self.assertEqual(planner.call_count,1)
   self.assertIn('сменила сайт',result['message'])
 def test_sensitive_desktop_action_is_handed_back_without_success_claim(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.desktop.current={'token':'selected'}
   w.desktop.act.side_effect=PermissionError('manual_confirmation_required')
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'desktop_invoke','element':'Send'}]}):
    result=engine.execute('Review selected window',use_model=True,allow_interaction=True)
   self.assertEqual(result['state'],'awaiting_manual');self.assertFalse(result['results'][0]['acted'])
   self.assertIn('не выполнялось',result['message'])
 def test_explicit_expected_text_is_verified_independently_after_action(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.browser.target='fixture';w.browser.observed={'context':9}
   w.browser.act.return_value={'acted':True,'observation':{'text':'RAINFORGE DONE'}}
   w.browser.wait_for.return_value={'verified':True,'condition':{'selector':'body','contains_text':'RAINFORGE DONE'},'actual':'RAINFORGE DONE'}
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'browser_click','selector':'#continue'}]}) as planner:
    result=engine.execute('Click Continue once then check that the page contains RAINFORGE DONE and report it.',use_model=True,allow_interaction=True)
   self.assertEqual(planner.call_count,1);self.assertEqual(result['state'],'checks_passed')
   self.assertEqual(result['proof']['condition']['contains_text'],'RAINFORGE DONE')
 def test_journal_failure_releases_controller_lock(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   with patch.object(engine,'save',side_effect=OSError('disk unavailable')):
    with self.assertRaises(OSError):engine.execute('open browser')
   self.assertFalse(engine.lock.locked());w.browser.navigate.assert_not_called()
 def test_generation_failure_cancels_owned_chat(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.begin_chat.return_value={'chat_id':'owned'}
   with patch.object(engine,'check_cancel',side_effect=[None,TimeoutError('deadline')]):
    with self.assertRaises(TimeoutError):engine.generate('test',200)
   w.cancel_chat.assert_called_once_with('owned');self.assertIsNone(engine.chat_id)
 def setup_worker(self,tmp):
  w=MagicMock();w.workspace_tools=WorkspaceTools(Path(tmp));w.desktop.current=None;w.browser.target=None
  w.poll_chat.return_value={'state':'completed','text':'Answer from observed data'}
  w.browser.navigate.return_value={'url':'https://example.org','title':'Observed','text':'Observed data'}
  return w,ActionEngine(w,Path(tmp))
 def test_observe_replan_answer_uses_actual_tool_history(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   plans=[{'actions':[{'tool':'search_web','query':'RainForge'}]},{'actions':[{'tool':'open_url','url':'https://example.org'}]},{'actions':[{'tool':'finish','summary':'Answer from observed data'}]}]
   with patch.object(engine,'model_plan',side_effect=plans) as planner:
    result=engine.execute('Research RainForge using the sources',use_model=True)
   self.assertEqual(len(result['results']),3);self.assertEqual(w.browser.navigate.call_count,2)
   self.assertIn('Observed data',str(planner.call_args.kwargs['history']))
   self.assertEqual(result['plan'][-1]['tool'],'finish');self.assertIn('Answer from observed data',result['message'])
 def test_repeat_is_blocked_before_second_side_effect(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);plan={'actions':[{'tool':'search_web','query':'Repeated'}]}
   with patch.object(engine,'model_plan',return_value=plan):
    with self.assertRaisesRegex(ValueError,'Повтор заблокирован'):engine.execute('Research this topic',use_model=True)
   self.assertEqual(w.browser.navigate.call_count,1)
 def test_budget_returns_partial_never_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   plans=[{'actions':[{'tool':'search_web','query':'query '+str(i)}]} for i in range(4)]
   with patch.object(engine,'model_plan',side_effect=plans),patch.object(engine,'generate',return_value='Observed summary'):
    result=engine.execute('Research this topic',use_model=True)
   self.assertEqual(result['state'],'partial');self.assertEqual(w.browser.navigate.call_count,4)
   self.assertIn('не подтверждено',result['message'])
 def test_model_cannot_invent_expected_condition(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'browser_assert','selector':'body','text':'Invented success'}]}):
    with self.assertRaisesRegex(ValueError,'задано пользователем'):engine.execute('Check that the task is complete',use_model=True)
   w.browser.wait_for.assert_not_called()

 def test_compound_native_goal_cannot_inspect_or_navigate_existing_browser(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.browser.target='unrelated'
   w.runtime.status.return_value={'state':'ready','model':'coder.gguf'}
   w.poll_chat.side_effect=[{'state':'completed','text':'{"actions":[{"tool":"open_app","app":"steam"}]}'},{'state':'completed','text':'{"actions":[{"tool":"finish","summary":"Уточните получателя и сообщение"}]}'},{'state':'completed','text':'Укажите получателя и текст. Сообщение не отправлялось.'}]
   w.workspace_tools.launch=MagicMock(return_value={'name':'Steam','verified_window':False})
   w.desktop.windows.return_value={'windows':[]}
   result=engine.execute('открой стим и отправь сообщение error',use_model=True)
   w.workspace_tools.launch.assert_called_once_with('steam')
   w.browser.observe.assert_not_called();w.browser.navigate.assert_not_called();w.desktop.act.assert_not_called()
   self.assertEqual(w.begin_chat.call_count,1) # Controller asks for permission, no fabricated model refusal.
   self.assertEqual(result['state'],'awaiting_permission');self.assertIn('автоматически сообщение не отправлялось',result['message'])
 def test_permission_resume_requires_grant_revalidates_window_and_does_not_launch_again(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.workspace_tools.launch=MagicMock(return_value={'name':'Steam'});w.desktop.windows.return_value={'windows':[{'handle':71,'pid':9,'title':'Steam'}]}
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'open_app','app':'steam'}]}) as planner:
    old=engine.execute('открой стим и посмотри окно',use_model=True)
   self.assertEqual(old['state'],'awaiting_permission');self.assertEqual(planner.call_count,1);w.desktop.inspect.assert_not_called()
   with self.assertRaisesRegex(ValueError,'Разрешите чтение'):engine.resume(old['id'],reviewed=True)
   w.desktop.inspect.return_value={'handle':71,'pid':9,'token':'fresh','elements':[]}
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'finish','summary':'Observed'}]}) as planner:
    run=engine.resume(old['id'],reviewed=True,allow_interaction=True);engine.thread.join(3)
   self.assertNotEqual(engine.status(run['id'])['state'],'failed');self.assertEqual(w.workspace_tools.launch.call_count,1)
   self.assertEqual(planner.call_args.args[1]['token'],'fresh')
 def test_stateful_native_scroll_can_repeat_only_after_observed_progress(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);base={'handle':1,'pid':2,'token':'fresh','elements':[{'id':'scroll','name':'List','scroll_down':True,'scroll_percent':0}]};w.desktop.current=base
   def scrolled(token,element,operation,text,**kwargs):
    import copy
    state=copy.deepcopy(w.desktop.current);state['elements'][0]['scroll_percent']+=10;w.desktop.current=state
    return {'acted':True,'observation':state}
   w.desktop.act.side_effect=scrolled
   plans=[{'actions':[{'tool':'desktop_scroll_down','element':'List'}]}]*2+[{'actions':[{'tool':'finish','summary':'Observed'}]}]
   with patch.object(engine,'model_plan',side_effect=plans):result=engine.execute('Scroll the selected list twice',use_model=True,allow_interaction=True)
   self.assertEqual(w.desktop.act.call_count,2);self.assertNotEqual(result['state'],'failed')
   w.desktop.current=base;w.desktop.act.side_effect=lambda *args,**kwargs:{'acted':True,'observation':base}
   with patch.object(engine,'model_plan',side_effect=lambda *a,**kw:{'actions':[{'tool':'desktop_scroll_down','element':'List'}]}):
    with self.assertRaisesRegex(ValueError,'Повтор заблокирован'):engine.execute('Scroll the selected list',use_model=True,allow_interaction=True)
 def test_compound_grant_binds_only_the_verified_launched_window(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   w.workspace_tools.launch=MagicMock(return_value={'name':'Steam'})
   w.desktop.windows.return_value={'windows':[{'handle':71,'pid':9,'title':'Steam'}]}
   selected={'token':'new','handle':71,'pid':9,'elements':[]};w.desktop.inspect.return_value=selected
   plans=[{'actions':[{'tool':'open_app','app':'steam'}]},{'actions':[{'tool':'finish','summary':'Окно открыто'}]}]
   with patch.object(engine,'model_plan',side_effect=plans) as planner:
    engine.execute('открой стим и посмотри окно',use_model=True,allow_interaction=True)
   w.desktop.inspect.assert_called_once_with(71,9);self.assertEqual(planner.call_args.args[1],selected)
 def test_extended_budget_does_not_stop_at_four_and_records_actual_duration(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   plans=[{'actions':[{'tool':'search_web','query':str(i)}]} for i in range(6)]+[{'actions':[{'tool':'finish','summary':'Observed'}]}]
   with patch.object(engine,'model_plan',side_effect=plans):result=engine.execute('Research online',use_model=True,budget='extended')
   self.assertEqual(w.browser.navigate.call_count,6);self.assertNotEqual(result['state'],'partial')
   self.assertEqual(result['max_steps'],32);self.assertEqual(result['max_seconds'],3600)
   self.assertGreaterEqual(result['completed_at'],result['started_at']);self.assertGreaterEqual(result['elapsed_seconds'],0)
 def test_resume_requires_review_and_never_replays_completed_actions(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   old={'id':'d'*32,'goal':'Research online','state':'partial','plan':[{'tool':'search_web','query':'done'}],'results':[{'url':'https://example.org','text':'Known observation'}]};engine.save(old)
   with self.assertRaises(ValueError):engine.resume(old['id'])
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'finish','summary':'Read checkpoint'}]}) as planner:
    result=engine.resume(old['id'],reviewed=True);engine.thread.join(3)
   resumed=engine.status(result['id']);self.assertEqual(resumed['parent_id'],old['id'])
   self.assertEqual(planner.call_args.kwargs['history'][0]['result']['text'],'Known observation');w.browser.navigate.assert_not_called()
   old['in_flight']={'tool':'search_web','query':'unknown'};old['state']='interrupted';engine.save(old)
   with self.assertRaisesRegex(ValueError,'Незавершённое'):engine.resume(old['id'],reviewed=True)
 def test_desktop_clause_preserves_catalog_identity(self):
  from rainforge_worker.actions import desktop_request
  catalog=MagicMock();catalog.resolve.return_value={'id':'installed:trusted'}
  plan,compound=desktop_request('открой тг и отправь сообщение error',catalog)
  self.assertTrue(compound);self.assertEqual(plan['actions'][0]['app'],'installed:trusted');catalog.resolve.assert_called_once_with('тг')
 def test_assertion_is_not_offered_without_user_expected_text(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.runtime.status.return_value={'state':'ready','model':'coder.gguf'};w.browser.target='page';w.browser.inspect.return_value={'url':'https://example.org','text':'Observed'}
   w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"finish","summary":"Read only"}]}'}
   engine.model_plan('Inspect the page')
   tools=w.begin_chat.call_args.kwargs['schema']['properties']['actions']['items']['oneOf']
   self.assertNotIn('browser_assert',[t['properties']['tool']['enum'][0] for t in tools])
 def test_budget_validation_precedes_actions_and_event_log_is_bounded(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp)
   for budget in ('unlimited',{},None):
    with self.assertRaises(ValueError):engine.start('Research',budget=budget)
   record={'id':'f'*32}
   for n in range(170):engine.event(record,str(n))
   self.assertEqual(len(engine.status(record['id'])['events']),160)

 def test_resumed_native_task_revalidates_its_window_not_unrelated_selection(self):
  with tempfile.TemporaryDirectory() as tmp:
   w,engine=self.setup_worker(tmp);w.desktop.current={'handle':999,'token':'unrelated'}
   old={'id':'c'*32,'goal':'открой стим и посмотри окно','state':'partial','plan':[{'tool':'open_app','app':'steam'}],'results':[{'verified_window':True,'window':{'handle':71,'pid':9}}]};engine.save(old)
   w.desktop.windows.return_value={'windows':[{'handle':71,'pid':9}]};w.desktop.inspect.return_value={'handle':71,'pid':9,'token':'fresh','elements':[]}
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'finish','summary':'Observed checkpoint'}]}) as planner:
    run=engine.resume(old['id'],reviewed=True,allow_interaction=True);engine.thread.join(3)
   self.assertEqual(planner.call_args.args[1]['token'],'fresh');w.desktop.inspect.assert_called_once_with(71,9)
   w.desktop.windows.return_value={'windows':[{'handle':71,'pid':88}]}
   run=engine.resume(old['id'],reviewed=True,allow_interaction=True);engine.thread.join(3)
   self.assertEqual(engine.status(run['id'])['state'],'failed');w.browser.navigate.assert_not_called()

if __name__=='__main__':unittest.main()
