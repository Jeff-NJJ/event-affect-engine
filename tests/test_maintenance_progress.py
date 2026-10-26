import json,tempfile,time,unittest
from pathlib import Path
from endocrine.clock import FrozenClock,VirtualLeaseClock
from endocrine.model import Config,Event,iso
from endocrine.store import Repository
from endocrine.service import Service

class MaintenanceProgressTests(unittest.TestCase):
    def test_expiry_enqueue_does_not_block_behind_active_classification(self):
        with tempfile.TemporaryDirectory() as tmp:
            clock=FrozenClock(1792288800);lease=VirtualLeaseClock(clock.now())
            repo=Repository(Path(tmp)/'m.db',Config(),lease_clock=lease);svc=Service(repo,clock=clock)
            svc.submit({'agent_id':'a','event_id':'expect','source':'system','provenance':'operator_assertion',
                'event_type':'expect_add','occurred_at':iso(clock.now()),'payload':{'id':'promise','expect':'明确的约定','mutual':True,'deadline':iso(clock.now()+10)}})
            clock.advance(600);lease.advance(600)
            repo.reserve(Event.parse({'agent_id':'a','event_id':'busy','actor_id':'p','text':'处理中','occurred_at':iso(clock.now())}),clock.now())
            busy=repo.claim_next('a');start=time.monotonic()
            svc.settle_due(wait_seconds=0);svc.settle_due(wait_seconds=0)
            self.assertLess(time.monotonic()-start,1)
            expiry=[r for r in repo.export()['events'] if json.loads(r['body'])['event_type']=='expect_resolve']
            self.assertEqual(len(expiry),1);self.assertEqual(expiry[0]['status'],'pending')
            svc._process(busy);svc.recover_pending(limit=1)
            self.assertEqual(repo.load_state('a',clock.now())['expectations']['promise']['status'],'expired_unknown')

if __name__=='__main__':unittest.main()
