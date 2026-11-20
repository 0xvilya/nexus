import sys,time,tempfile,unittest,threading
from pathlib import Path
from unittest.mock import MagicMock,patch
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'));import bootstrap
from rainforge_core import NexusService
from rainforge_worker.rpc import Worker
from rainforge_worker.actions import ActionEngine

class ConversationTests(unittest.TestCase):
 def test_action_results_share_thread_and_block_overlapping_turns(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=MagicMock();worker.core=NexusService(Path(tmp));thread=worker.core.conversation_create()['id'];engine=ActionEngine(worker,Path(tmp));gate=threading.Event()
   def execute(goal,**kwargs):gate.wait(2);kwargs['record'].update(state='observed',message='Observed fixture result')
   with patch.object(engine,'execute',side_effect=execute):
    started=engine.start('Explicit fixture task',conversation_id=thread)
    self.assertEqual(started['conversation_id'],thread)
    with self.assertRaises(ValueError):worker.core.conversation_begin(thread,'overlapping turn')
    gate.set();engine.thread.join(3)
   messages=worker.core.conversation_read(thread)['messages']
   self.assertEqual(len(messages),2);self.assertEqual(messages[-1]['kind'],'action');self.assertEqual(messages[-1]['text'],'Observed fixture result')
   engine.close()
 def test_many_messages_pagination_isolation_and_restart(self):
  with tempfile.TemporaryDirectory() as tmp:
   core=NexusService(Path(tmp));a=core.conversation_create()['id'];b=core.conversation_create()['id']
   for i in range(90):
    seq=core.conversation_begin(a,'message '+str(i));core.conversation_update(a,seq,'answer '+str(i),'completed')
   page=core.conversation_read(a);self.assertEqual(len(page['messages']),50);self.assertTrue(page['has_more'])
   seen=page['messages']
   while page['has_more']:
    page=core.conversation_read(a,before=page['messages'][0]['seq']);seen=page['messages']+seen
   self.assertEqual(len(seen),180);self.assertEqual(len({m['seq'] for m in seen}),180)
   self.assertEqual(core.conversation_read(b)['messages'],[])
   self.assertLessEqual(len(core.conversation_context(a)),4500)
   self.assertIn('message 89',core.conversation_context(a));self.assertNotIn('message 0\n',core.conversation_context(a))
   recovered=NexusService(Path(tmp));self.assertEqual(recovered.conversation_list()['items'][0]['messages'],180)
   with self.assertRaises(ValueError):recovered.conversation_read('../unsafe')
 def test_only_one_turn_and_interruption_never_replays(self):
  with tempfile.TemporaryDirectory() as tmp:
   core=NexusService(Path(tmp));a=core.conversation_create()['id'];seq=core.conversation_begin(a,'first')
   core.conversation_update(a,seq,'partial','running')
   with self.assertRaises(ValueError):core.conversation_begin(a,'second')
   recovered=NexusService(Path(tmp));page=recovered.conversation_read(a)
   self.assertEqual(page['messages'][-1]['state'],'interrupted');self.assertEqual(page['messages'][-1]['text'],'partial')
   recovered.conversation_begin(a,'new explicit message')
 def test_worker_saves_turn_and_passes_bounded_history(self):
  with tempfile.TemporaryDirectory() as tmp:
   worker=Worker(Path(tmp),bootstrap.ROOT/'runtime');worker.runtime=MagicMock();worker.runtime.status.return_value={'state':'ready'}
   prompts=[]
   def generate(prompt,emit,cancelled,*args,**kwargs):prompts.append((prompt,kwargs.get('history',[])));emit('saved response')
   worker.runtime.generate.side_effect=generate
   try:
    first=worker.begin_user_chat('Remember the fictional codeword orange ship');thread=first['conversation_id']
    for _ in range(100):
     if worker.poll_chat(first['chat_id'])['state']!='running':break
     time.sleep(.01)
    second=worker.begin_user_chat('What codeword did I mention?',conversation_id=thread)
    for _ in range(100):
     if worker.poll_chat(second['chat_id'])['state']!='running':break
     time.sleep(.01)
    page=worker.read_conversation(thread);self.assertEqual(len(page['messages']),4)
    self.assertTrue(all(m['state']=='completed' for m in page['messages']))
    self.assertEqual(prompts[-1][0],'What codeword did I mention?')
    self.assertEqual(prompts[-1][1],[
     {'role':'user','content':'Remember the fictional codeword orange ship'},
     {'role':'assistant','content':'saved response'},
    ])
    self.assertEqual(page['messages'][-1]['text'],'saved response')
    self.assertNotIn('data:image',str(page))
    for i in range(23):
     reply=worker.begin_user_chat('extra '+str(i),conversation_id=thread)
     for _ in range(100):
      if worker.poll_chat(reply['chat_id'])['state']!='running':break
      time.sleep(.01)
    self.assertLessEqual(len(worker.chats),20)
    self.assertEqual(worker.core.conversation_list()['items'][0]['messages'],50)
    self.assertIn('orange ship',worker.read_conversation(thread)['messages'][0]['text'])
    def slow(prompt,emit,cancelled,*args,**kwargs):emit('partial answer');cancelled.wait(3)
    worker.runtime.generate.side_effect=slow
    running=worker.begin_user_chat('slow reply',conversation_id=thread)
    active=worker.read_conversation(thread)['active_chat'];self.assertEqual(active['chat_id'],running['chat_id'])
    worker.cancel_chat(running['chat_id'])
    for _ in range(100):
     if worker.poll_chat(running['chat_id'])['state']!='running':break
     time.sleep(.01)
    self.assertEqual(worker.read_conversation(thread)['messages'][-1]['state'],'cancelled')
   finally:worker.close()

if __name__=='__main__':unittest.main()
