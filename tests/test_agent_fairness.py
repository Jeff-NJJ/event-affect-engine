import concurrent.futures,tempfile,unittest
from pathlib import Path
from endocrine.model import Config,Event,iso
from endocrine.clock import FrozenClock,VirtualLeaseClock
from endocrine.store import Repository
from endocrine.service import Service

class FairRecoveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.path=Path(self.tmp.name)/'f.db';self.clock=VirtualLeaseClock(1792288800)
        self.repo=Repository(self.path,Config(),lease_clock=self.clock);self.svc=Service(self.repo,clock=FrozenClock(self.clock.wall()))
    def reserve(self,agent,eid):
        return self.repo.reserve(Event.parse({'agent_id':agent,'event_id':eid,'actor_id':'p','occurred_at':iso(self.clock.wall()),'text':'合成消息'}),self.clock.wall())
    def test_large_backlog_does_not_starve_other_agent(self):
        for n in range(30):self.reserve('a',str(n))
        self.reserve('b','waiting')
        self.svc.recover_pending(limit=1);self.svc.recover_pending(limit=1)
        self.assertEqual(self.repo.get_event('b','waiting')['status'],'applied')
        self.assertEqual(sum(r['status']=='applied' for r in self.repo.export()['events']),2)
    def test_fair_turn_survives_repository_restart(self):
        self.reserve('a','1');self.reserve('a','2');self.reserve('b','1')
        self.svc.recover_pending(limit=1)
        other=Service(Repository(self.path,Config(),lease_clock=self.clock),clock=self.svc.clock)
        other.recover_pending(limit=1)
        self.assertEqual(self.repo.get_event('b','1')['status'],'applied')
    def test_inflight_head_is_not_overtaken_but_other_agent_progresses(self):
        self.reserve('a','head');self.reserve('a','tail');self.reserve('b','head')
        self.repo.claim_next('a');self.svc.recover_pending(limit=2)
        self.assertEqual(self.repo.get_event('a','tail')['status'],'pending')
        self.assertEqual(self.repo.get_event('b','head')['status'],'applied')
    def test_concurrent_recovery_claims_each_head_once(self):
        for n in range(8):self.reserve(str(n),'one')
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            claimed=list(pool.map(lambda _:self.repo.claim_next_any(),range(16)))
        claimed=[r for r in claimed if r]
        self.assertEqual(len(claimed),8);self.assertEqual(len({r['seq'] for r in claimed}),8)

if __name__=='__main__':unittest.main()
