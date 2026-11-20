import json,sys,tempfile,time,unittest,os
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_worker.actions import ActionEngine,plan_schema
from rainforge_worker.workspace import WorkspaceTools

class AITaskTests(unittest.TestCase):
 @unittest.skipUnless(os.name=='nt' and os.environ.get('RF_DESKTOP_TEST')=='1','Opt-in native calculator widget fixture')
 def test_trusted_keypad_component_repairs_duplicated_buttons_and_callbacks(self):
  import tkinter as tk
  from rainforge_worker.actions import NUMERIC_HELPER
  scope={};exec(NUMERIC_HELPER,scope);root=tk.Tk();root.title('RainForge calculator component fixture')
  try:
   display=tk.Entry(root);display.grid(row=0,column=0,columnspan=2)
   equal=tk.Button(root,text='=',command=lambda:display.insert('end','='));equal.grid(row=1,column=1)
   duplicate=tk.Button(root,text='=');duplicate.grid(row=2,column=1)
   clear=tk.Button(root,text='Clear');clear.grid(row=1,column=0,columnspan=2)
   scope['rainforge_finalize_calculator'](root);root.update()
   display.insert(0,'2+3*4');equal.invoke();self.assertEqual(float(display.get()),14)
   clear.invoke();self.assertEqual(display.get(),'')
   display.insert(0,'1/0');equal.invoke();self.assertIn('Error',display.get())
   self.assertFalse(duplicate.winfo_ismapped())
   self.assertNotEqual(equal.grid_info()['row'],clear.grid_info()['row'])
   self.assertGreaterEqual(root.winfo_width(),root.winfo_reqwidth())
  finally:root.destroy()
 def test_targeted_model_handler_repair_and_nonoverlapping_keypad(self):
  from rainforge_worker.actions import python_findings
  import ast
  source='import tkinter as tk\nclass Calculator:\n def __init__(self,root):\n  self.display=tk.Entry(root)\n  self.buttons=[("0",1,0),("C",1,0)]\n  self.button=tk.Button(root,command=self.clear)\n'
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   w.poll_chat.side_effect=[{'state':'completed','text':source},{'state':'completed','text':'def clear(self):\n    self.display.delete(0, "end")\n'}]
   result=engine.create_python('write a Python calculator','calculator.py',{'id':'e'*32})
   tree=ast.parse(result['text']);self.assertEqual(python_findings(tree,True),[])
   self.assertIn('def clear(self)',result['text']);self.assertIn("('C', 2, 0)",result['text'])
   self.assertEqual(w.begin_chat.call_count,2)
 def test_model_answer_has_no_fabricated_tool_success(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp));w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"finish","summary":"Объяснение модели"}]}'}
   result=engine.execute('Объясни термин',use_model=True)
   self.assertEqual(result['state'],'answered');self.assertIn('действия на компьютере не выполнялись',result['message'])
   w.browser.navigate.assert_not_called()
 def test_opted_in_explicit_memory_is_not_proposed_again_by_action_model(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);w.core.memory_capture_user.return_value={'state':'saved','note_id':'owned'}
   w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"finish","summary":"Запомнил"}]}'}
   engine=ActionEngine(w,Path(tmp));result=engine.execute('запомни: предпочитаю unittest',use_model=True)
   first=w.begin_chat.call_args_list[0].kwargs['schema']['properties']['actions']['items']['oneOf']
   self.assertEqual([v['properties']['tool']['enum'] for v in first],[['finish']]);w.core.memory_propose.assert_not_called()
   self.assertEqual(result['memory_capture'],'saved')
 def test_image_question_cannot_silently_open_browser(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp));w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"finish","summary":"На разрешённом снимке окно"}]}'}
   result=engine.execute('Что на снимке?',use_model=True,image='data:image/jpeg;base64,/9j/2Q==',vision_filename='vision.gguf')
   options=w.begin_chat.call_args_list[0].kwargs['schema']['properties']['actions']['items']['oneOf']
   self.assertEqual([o['properties']['tool']['enum'] for o in options],[['finish']])
   self.assertEqual(result['state'],'answered');w.browser.inspect.assert_not_called();w.browser.navigate.assert_not_called()
 def test_generated_dynamic_execution_is_rejected_before_saving(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   w.poll_chat.return_value={'state':'completed','text':'def calculate(text):\n    return eval(text)\n\nif __name__ == "__main__":\n    print(calculate(input()))\n'}
   with self.assertRaises(ValueError):engine.create_python('write Python reader','reader.py',{'id':'d'*32})
   self.assertEqual(w.workspace_tools.listing()['files'],[])
 def test_trusted_numeric_helper_rejects_code_and_limits_work(self):
  from rainforge_worker.actions import NUMERIC_HELPER
  scope={};exec(NUMERIC_HELPER,scope);calculate=scope['rainforge_calculate']
  self.assertEqual(calculate('2+3*4'),14);self.assertEqual(calculate('-(7-2)/2'),-2.5)
  for value in ("__import__('os')", '2**10000000','[1]*1000','True','1e300','1+'*81+'1'):
   with self.assertRaises((ValueError,SyntaxError)):calculate(value)
  with self.assertRaises(ZeroDivisionError):calculate('1/0')
 def worker(self,tmp):
  worker=MagicMock();worker.workspace_tools=WorkspaceTools(Path(tmp));worker.desktop.current=None;worker.browser.target=None
  worker.runtime.status.return_value={'state':'ready','model':'coder.gguf'}
  worker.begin_chat.return_value={'chat_id':'test'};return worker
 def test_ai_even_for_known_application_and_no_substitution(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"open_app","app":"steam"}]}'}
   w.desktop.windows.return_value={'windows':[]};w.workspace_tools.launch=MagicMock(return_value={'app':'steam','state':'launch_requested'})
   engine.execute('открой стим',use_model=True)
   self.assertEqual(w.begin_chat.call_count,1)
   schema=w.begin_chat.call_args.kwargs['schema'];self.assertEqual(schema['properties']['actions']['maxItems'],1)
   w.workspace_tools.launch.assert_called_once_with('steam');w.browser.navigate.assert_not_called()
 def test_unterminated_json_retries_with_schema_and_no_actions(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   w.poll_chat.return_value={'state':'completed','text':'{"actions":[{"tool":"open_url","url":"unterminated'}
   with self.assertRaisesRegex(ValueError,'неполный план'):engine.execute('Проведи исследование темы',use_model=True)
   self.assertEqual(w.begin_chat.call_count,2);w.browser.navigate.assert_not_called()
   self.assertTrue(all('schema' in c.kwargs for c in w.begin_chat.call_args_list))
 def test_generated_code_is_separate_from_json_and_export_is_explicit(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as desktop:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   w.poll_chat.side_effect=[{'state':'completed','text':'{"actions":[{"tool":"create_python","path":"calculator.py"}]}'},{'state':'completed','text':'def add(a, b):\n    return a + b\n\nif __name__ == "__main__":\n    print(add(2, 3))\n'}]
   result=engine.execute('напиши калькулятор на пайтоне на моем рабочем столе',use_model=True)
   self.assertEqual(result['state'],'file_created');self.assertTrue(result['results'][0]['syntax_verified'])
   self.assertFalse(list(Path(desktop).iterdir()));self.assertEqual(w.begin_chat.call_count,2)
   with patch('rainforge_worker.workspace.desktop_directory',return_value=Path(desktop)):
    exported=engine.export_desktop(result['id']);self.assertTrue(Path(exported['path']).is_file())
    with self.assertRaisesRegex(ValueError,'уже есть'):engine.export_desktop(result['id'])
   self.assertEqual(Path(exported['path']).read_bytes(),w.workspace_tools.read('calculator.py')['text'].encode('utf-8'))
 def test_invalid_python_and_changed_export_leave_existing_files(self):
  with tempfile.TemporaryDirectory() as tmp,tempfile.TemporaryDirectory() as desktop:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   w.poll_chat.return_value={'state':'completed','text':'def incomplete('}
   with self.assertRaisesRegex(ValueError,'некорректный'):engine.create_python('write Python calculator','app.py',{'id':'a'*32,'results':[]})
   self.assertFalse(w.workspace_tools.path('app.py').exists())
   w.poll_chat.return_value={'state':'completed','text':'def add(a,b):\n    return a+b\n\nif __name__ == "__main__":\n    print(add(1,2))\n'}
   record={'id':'b'*32,'state':'file_created','results':[]};record['results']=[engine.create_python('write Python','app.py',record)];record['state']='file_created';engine.save(record)
   original=w.workspace_tools.read('app.py');w.workspace_tools.write('app.py','changed',original['sha256'])
   with patch('rainforge_worker.workspace.desktop_directory',return_value=Path(desktop)):
    with self.assertRaisesRegex(ValueError,'изменён'):engine.export_desktop(record['id'])
   self.assertFalse(list(Path(desktop).iterdir()))
 def test_mutation_and_code_permissions_are_checked_before_tools(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp))
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'desktop_invoke','element':'Send'}]}):
    with self.assertRaisesRegex(ValueError,'Разрешите'):engine.execute('Нажми кнопку',use_model=True)
   w.desktop.act.assert_not_called()
   with patch.object(engine,'model_plan',return_value={'actions':[{'tool':'create_python','path':'bad.py'}]}):
    with self.assertRaisesRegex(ValueError,'не разрешено'):engine.execute('Прочитай страницу',use_model=True)
   self.assertEqual(w.workspace_tools.listing()['files'],[])
 def test_background_cancel_prevents_write_and_recovers_interrupted(self):
  with tempfile.TemporaryDirectory() as tmp:
   w=self.worker(tmp);engine=ActionEngine(w,Path(tmp));w.poll_chat.return_value={'state':'running','text':''}
   run=engine.start('напиши программу на python')
   for _ in range(100):
    if engine.chat_id:break
    time.sleep(.01)
   for _ in range(100):self.assertIn(engine.status(run['id'])['state'],{'planning','generating'})
   engine.cancel();engine.thread.join(3);self.assertEqual(engine.status(run['id'])['state'],'cancelled')
   self.assertEqual(w.workspace_tools.listing()['files'],[])
   stale={'id':'c'*32,'goal':'test','state':'generating','results':[]};engine.save(stale)
   recovered=ActionEngine(w,Path(tmp));self.assertEqual(recovered.status(stale['id'])['state'],'interrupted')
   with self.assertRaises(ValueError):recovered.status('../outside')

if __name__=='__main__':unittest.main()
