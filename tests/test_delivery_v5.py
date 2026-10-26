import concurrent.futures
import tempfile
import unittest
from pathlib import Path
from endocrine.clock import VirtualLeaseClock
from endocrine.delivery import DeliveryService
from endocrine.model import Config, DomainError
from endocrine.store import Repository


class Gateway:
    def __init__(self):self.calls=0;self.state={};self.fail_after_send=False;self.accept_only=False
    def send(self,operation_id,payload):
        self.calls+=1
        self.state[operation_id]={'operation_id':operation_id,'status':'accepted' if self.accept_only else 'delivered','message_id':'m1'}
        if self.fail_after_send:raise TimeoutError()
        return self.state[operation_id]
    def lookup(self,operation_id):return self.state.get(operation_id,{'operation_id':operation_id,'status':'not_found'})


class DeliveryTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.clock=VirtualLeaseClock(10000)
        self.repo=Repository(Path(self.tmp.name)/'d.db',Config(),lease_clock=self.clock)
        self.gateway=Gateway();self.service=DeliveryService(self.repo,self.gateway)
        self.payload={'agent_id':'a','conversation_id':'c','text':'测试消息'}
        self.service.queue('op',self.payload)

    def test_same_id_queue_and_dispatch_once_under_concurrency(self):
        self.service.queue('op',self.payload)
        with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
            list(pool.map(lambda _:self.service.dispatch('op'),range(16)))
        self.assertEqual(self.gateway.calls,1)
        self.assertEqual(self.service.view('op')['status'],'delivered')
        with self.assertRaises(DomainError):self.service.queue('op',{**self.payload,'text':'changed'})

    def test_lost_receipt_reconciled_without_resend(self):
        self.gateway.fail_after_send=True
        self.assertEqual(self.service.dispatch('op')['status'],'unknown_delivery')
        self.assertEqual(self.service.dispatch('op')['status'],'unknown_delivery')
        self.assertEqual(self.service.reconcile('op')['status'],'delivered')
        self.assertEqual(self.gateway.calls,1)

    def test_accepted_is_not_delivered(self):
        self.gateway.accept_only=True
        self.assertEqual(self.service.dispatch('op')['status'],'accepted')
        self.assertEqual(self.service.reconcile('op')['status'],'accepted')
        self.gateway.state['op'].update(status='delivered')
        self.assertEqual(self.service.reconcile('op')['status'],'delivered')

    def test_crash_after_claim_and_clock_rollback_reconciles(self):
        self.repo.claim_action('op',self.clock.wall())
        self.gateway.state['op']={'operation_id':'op','status':'delivered','message_id':'remote'}
        self.clock.rollback_wall(7200)
        self.assertEqual(self.service.reconcile('op')['status'],'delivered')
        self.assertEqual(self.gateway.calls,0)

    def test_ambiguous_not_found_never_retries(self):
        self.repo.claim_action('op',self.clock.wall());self.clock.advance(121)
        self.assertEqual(self.service.reconcile('op')['status'],'unknown_delivery')
        self.service.dispatch('op');self.assertEqual(self.gateway.calls,0)

    def test_authoritative_negative_receipt_with_dedupe_allows_same_id(self):
        self.repo.claim_action('op',self.clock.wall());self.clock.advance(121)
        self.gateway.state['op']={'operation_id':'op','status':'not_found','authoritative':True,'dedupe_supported':True}
        self.assertEqual(self.service.reconcile('op')['status'],'ready')
        self.assertEqual(self.service.dispatch('op')['status'],'delivered')
        self.assertEqual(self.gateway.calls,1)

    def test_wrong_receipt_or_missing_delivery_id_never_claims_success(self):
        for bad in ({'operation_id':'else','status':'delivered','message_id':'m'}, {'operation_id':'op','status':'delivered'}):
            with self.subTest(bad=bad):
                self.gateway.send=lambda *args:bad
                self.assertEqual(self.service.dispatch('op')['status'],'unknown_delivery')
                with self.repo.transaction() as c:c.execute("UPDATE outbox SET status='ready' WHERE operation_id='op'")

    def test_no_gateway_keeps_ready_and_reports_missing_configuration(self):
        service=DeliveryService(self.repo)
        with self.assertRaises(DomainError):service.dispatch('op')
        self.assertEqual(service.view('op')['status'],'ready')

    def test_unknown_old_operation_does_not_starve_new_ready(self):
        with self.repo.transaction() as c:c.execute("UPDATE outbox SET status='unknown_delivery' WHERE operation_id='op'")
        self.service.queue('new',self.payload)
        def lookup(operation_id):raise TimeoutError()
        self.gateway.lookup=lookup
        for _ in range(100):self.service.maintain(limit=1);self.clock.advance(1)
        self.assertEqual(self.service.view('new')['status'],'delivered')
        self.assertEqual(self.service.view('op')['status'],'unknown_delivery')
        self.assertEqual(self.gateway.calls,1)
