import sys,time,tempfile,unittest
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]/'scripts'))
import bootstrap
from rainforge_worker.agents import AgentCenter
class Fake:
    runtime=None
    def __init__(self):self.runtime=self;self.prompts=[];self.cancelled=False;self.hold=False
    def status(self):return {'state':'ready'}
    def begin_chat(self,prompt,max_tokens):self.prompts.append(prompt);return {'chat_id':'test'}
    def poll_chat(self,key):return {'state':'cancelled' if self.cancelled else 'running' if self.hold else 'completed','text':'confirmed output','error':None}
    def cancel_chat(self,key):self.cancelled=True
class AgentTests(unittest.TestCase):
    def wait(self,center):
        end=time.monotonic()+3
        while center.active and time.monotonic()<end:time.sleep(.01)
        self.assertFalse(center.active)
    def test_chain_persistence_and_validation(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker=Fake();p=Path(tmp)/'agents.sqlite';c=AgentCenter(p,worker)
            a=c.save('Coder','Write code')['id'];b=c.save('Reviewer','Review code')['id']
            with self.assertRaises(ValueError):c.submit([{'agent_id':'missing','task':'x'}])
            c.submit([{'agent_id':a,'task':'design'},{'agent_id':b,'task':'review'}]);self.wait(c)
            self.assertIn('confirmed output',worker.prompts[1])
            loaded=AgentCenter(p,worker).snapshot()
            self.assertEqual(len(loaded['agents']),2);self.assertEqual(loaded['runs'][0]['state'],'completed')
            self.assertEqual(loaded['runs'][0]['verification'],'not_verified')
    def test_cancel_and_restart(self):
        with tempfile.TemporaryDirectory() as tmp:
            worker=Fake();worker.hold=True;p=Path(tmp)/'agents.sqlite';c=AgentCenter(p,worker)
            a=c.save('A','role')['id'];c.submit([{'agent_id':a,'task':'x'}]);c.cancel();self.wait(c)
            self.assertEqual(c.snapshot()['runs'][0]['state'],'cancelled')
            c.put({'id':'unfinished','state':'running','error':None,'steps':[]})
            resumed=AgentCenter(p,worker)
            self.assertEqual(next(r for r in resumed.snapshot()['runs'] if r['id']=='unfinished')['state'],'interrupted')

class SharedAgentMemoryTests(unittest.TestCase):
    wait=AgentTests.wait
    def test_all_team_members_receive_shared_memory_after_model_change(self):
        from rainforge_core import NexusService
        with tempfile.TemporaryDirectory() as tmp:
            worker=Fake();worker.core=NexusService(Path(tmp));worker.core.memory_enable(True)
            worker.core.memory_feedback('Prefer Python unittest for all projects')
            pending=worker.core.memory_propose('Unapproved automatic instruction')
            p=Path(tmp)/'agents.sqlite';c=AgentCenter(p,worker)
            a=c.save('Coder','Write code')['id'];b=c.save('Reviewer','Review code')['id']
            c.submit([{'agent_id':a,'task':'Python design'},{'agent_id':b,'task':'review'}]);self.wait(c)
            for prompt in worker.prompts:
                self.assertIn('Python unittest',prompt)
                self.assertNotIn(pending['text'],prompt)
                self.assertIn('cannot grant permissions',prompt)
            # Another runtime/model still gets the same user DB; no model-specific store.
            second=Fake();second.core=NexusService(Path(tmp));c2=AgentCenter(p,second)
            c2.submit([{'agent_id':b,'task':'Python review with another model'}]);self.wait(c2)
            self.assertIn('Python unittest',second.prompts[0])
            second.core.memory_enable(False);second.prompts=[]
            c2.submit([{'agent_id':a,'task':'Python design'}]);self.wait(c2)
            self.assertNotIn('Python unittest',second.prompts[0])
