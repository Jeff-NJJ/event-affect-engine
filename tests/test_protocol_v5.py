import inspect
import json
import tempfile
import threading
import unittest
import urllib.error
import urllib.request
from pathlib import Path
from endocrine.api import make_server
from endocrine.clock import FrozenClock
from endocrine.model import Config,iso
from endocrine.service import Service
from endocrine.store import Repository


class ProtocolV5Tests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup)
        self.now=1792141200
        self.service=Service(Repository(Path(self.tmp.name)/'a.db',Config()),clock=FrozenClock(self.now))
        self.server=make_server(self.service,port=0,token='local-fixture',admin_token='operator-fixture')
        self.thread=threading.Thread(target=self.server.serve_forever,daemon=True);self.thread.start()
        self.base='http://127.0.0.1:'+str(self.server.server_port)
        self.addCleanup(self.close)

    def close(self):
        self.server.shutdown();self.server.server_close();self.thread.join(timeout=3)

    def request(self,path,body=None,admin=False):
        req=urllib.request.Request(self.base+path,data=json.dumps(body).encode() if body is not None else None,
            headers={'X-Token':'local-fixture','Content-Type':'application/json',**({'X-Admin-Token':'operator-fixture'} if admin else {})})
        try:
            with urllib.request.urlopen(req,timeout=3) as r:return r.status,json.load(r)
        except urllib.error.HTTPError as e:return e.code,json.load(e)

    def test_future_clock_rejection_contains_server_time_and_calibrated_retry(self):
        status,clock=self.request('/v2/time');self.assertEqual(status,200)
        event={'agent_id':'a','event_id':'drift','occurred_at':iso(self.now+7200),'text':'测试'}
        status,error=self.request('/v2/events',event)
        self.assertEqual(status,400);self.assertEqual(error['error'],'event_too_far_in_future')
        self.assertEqual(error['server_time'],clock['server_time'])
        event['clock_sync']={'client_at_sync':iso(self.now+7200),'server_at_sync':clock['server_time']}
        status,result=self.request('/v2/events',event);self.assertEqual(status,200)
        status,retry=self.request('/v2/events',event);self.assertTrue(retry['deduplicated'])

    def test_memory_endpoint_pagination_and_filter(self):
        self.request('/emotion',{'msg':'测试','event_id':'1','agent_id':'a','actor_id':'p'})
        status,result=self.request('/v2/memory?agent_id=a&actor_id=p&limit=1')
        self.assertEqual(status,200);self.assertEqual(result['items'][0]['event_id'],'1')

    def test_delivery_queue_status_and_absent_gateway(self):
        payload={'agent_id':'a','conversation_id':'c','text':'提醒'}
        status,result=self.request('/v2/deliveries',{'operation_id':'op','payload':payload})
        self.assertEqual(status,200);self.assertEqual(result['status'],'ready')
        self.assertEqual(self.request('/v2/deliveries/status?operation_id=op')[1]['status'],'ready')
        self.assertEqual(self.request('/v2/deliveries/dispatch',{'operation_id':'op'})[0],503)

    def test_timeout_chain_budgets(self):
        budget=inspect.signature(Service.submit).parameters['wait_seconds'].default
        self.assertGreaterEqual(budget,70)
        project=Path(__file__).resolve().parents[1]
        for name in ('endocrine_api.js','endocrine_bridge.js'):
            script=(project/'operit'/name).read_text(encoding='utf-8')
            self.assertIn("slow ? '160' : '90'",script)
            self.assertIn('timeoutMs:slow ? 170000 : 190000',script)

    def test_operator_event_needs_separate_admin_token(self):
        raw={'agent_id':'a','event_id':'profile','source':'system','provenance':'operator_assertion',
            'event_type':'profile_update','occurred_at':iso(self.now),'payload':{'sensitivity':.8}}
        self.assertEqual(self.request('/v2/events',raw)[0],403)
        self.assertEqual(self.request('/v2/events',raw,admin=True)[0],200)
        self.assertEqual(self.service.repo.load_state('a',self.now)['personality']['sensitivity'],.8)

    def test_assistant_record_is_context_only_and_deduplicated(self):
        raw={'agent_id':'a','event_id':'reply','role':'assistant','conversation_id':'c','text':'我今天有些担心。'}
        status,first=self.request('/v2/messages',raw);self.assertEqual(status,200)
        self.assertEqual(first['events_processed'],0)
        self.assertTrue(self.request('/v2/messages',raw)[1]['deduplicated'])
        self.assertEqual(first['relationships'],{})

    def test_internal_resolution_cannot_be_injected(self):
        raw={'agent_id':'a','event_id':'r','event_type':'semantic_resolution'}
        self.assertEqual(self.request('/v2/events',raw,admin=True)[0],403)

    def test_post_search_accepts_whole_query_without_url_limit(self):
        from unittest.mock import patch
        query='长段落查询里的否定和转折都要保留。'*300
        with patch.object(self.service.semantic,'search',return_value={'success':True,'items':[]}) as search:
            self.assertEqual(self.request('/v2/memory/search',{'agent_id':'a','query':query})[0],200)
            self.assertEqual(search.call_args.args[1],query)
