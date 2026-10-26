"""Real official SDK client/server over both JSON and SSE. Synthetic memory only."""
import asyncio
import importlib.util
import hmac
import hashlib
import json
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time
import unittest
from datetime import datetime,timezone

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from endocrine.model import digest
from ob_bridge.mcp_client import MCPClient
from ob_bridge.backend import MCPBackend
from ob_bridge.service import BridgeService
from ob_bridge.store import ArchiveStore


class Manager:
    def __init__(self):
        self.records={'m1':{'id':'m1','content':'用户希望公开个人信息前先征求同意。',
            'metadata':{'type':'dynamic','created':'2026-10-01T10:00:00+08:00','activation_count':4}}}
        self.touches=0;self.creates=0
    async def get(self,bid):return self.records.get(bid)


class RealMCPTests(unittest.TestCase):
    def start(self,json_response):
        from mcp.server.fastmcp import FastMCP
        import uvicorn
        spec=importlib.util.spec_from_file_location('affect_bridge_tools',ROOT/'ob_extension/affect_bridge_tools.py')
        module=importlib.util.module_from_spec(spec);spec.loader.exec_module(module)
        self.temp=tempfile.TemporaryDirectory();self.addCleanup(self.temp.cleanup)
        self.key=b'synthetic-archive-secret-not-a-real-key-32'
        keyfile=Path(self.temp.name)/'test.key';keyfile.write_bytes(self.key)
        self.manager=Manager()
        s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
        mcp=FastMCP('OB synthetic',host='127.0.0.1',port=port,stateless_http=True,json_response=json_response)
        self.ext=module.register(mcp,self.manager,{'enabled':True,'archive_enabled':True,
            'archive_agents':['a'],'archive_db':str(Path(self.temp.name)/'ob_owned.sqlite'),'archive_signing_key_file':str(keyfile),
            'scopes':{'ap':{'bucket_ids':['m1'],'source_utc_offset_minutes':480}}})
        config=uvicorn.Config(mcp.streamable_http_app(),host='127.0.0.1',port=port,log_level='critical',access_log=False)
        server=uvicorn.Server(config)
        thread=threading.Thread(target=server.run,daemon=True);thread.start()
        deadline=time.monotonic()+10
        while not server.started and time.monotonic()<deadline:time.sleep(.02)
        self.assertTrue(server.started)
        self.addCleanup(lambda:(setattr(server,'should_exit',True),thread.join(5)))
        return MCPClient('http://127.0.0.1:'+str(port)+'/mcp')

    def run_contract(self,json_response):
        client=self.start(json_response)
        discovery=client.request('discover',timeout=10)
        hashes=discovery['tool_schema_hashes']
        self.assertEqual(set(hashes),{'affect_context_v1','affect_archive_v1','affect_archive_lookup_v1'})
        result=client.request('call',tool='affect_context_v1',schema_hash=hashes['affect_context_v1'],
            arguments={'scope_id':'ap','as_of':datetime.now(timezone.utc).isoformat(),'cursor':0},timeout=10)
        self.assertEqual(result['items'][0]['id'],'m1');self.assertFalse(result['memory_mutated'])
        self.assertEqual(self.manager.records['m1']['metadata']['activation_count'],4)
        item={'archive_id':'conclusion-1','agent_id':'a','actor_id':'p','source_event_id':'e1','source_seq':1,
            'occurred_at':'2026-10-18T05:00:00Z','known_at':'2026-10-18T05:00:00Z',
            'conclusion':{'trust_delta':-.01,'expectation_status':None,'classifier_status':'model',
                'interpretation':'agent_assessment_not_observed_world_fact'},
            'origin_system':'event_affect','lineage':['event_affect:a:e1']}
        for _ in range(2):
            from endocrine.model import canonical
            signature=hmac.new(self.key,canonical(item).encode(),hashlib.sha256).hexdigest()
            receipt=client.request('call',tool='affect_archive_v1',schema_hash=hashes['affect_archive_v1'],arguments={'item':item,'signature':signature},timeout=10)
            self.assertEqual(receipt['fingerprint'],digest(item))
        self.assertTrue(receipt['deduplicated']);self.assertEqual(self.manager.creates,0)
        lookup=client.request('call',tool='affect_archive_lookup_v1',schema_hash=hashes['affect_archive_lookup_v1'],
            arguments={'archive_id':'conclusion-1'},timeout=10)
        self.assertEqual(lookup['status'],'stored')
        from endocrine.model import DomainError
        with self.assertRaises(DomainError):client.request('call',tool='affect_archive_v1',schema_hash='0'*64,arguments={'item':item,'signature':signature},timeout=10)
        with self.assertRaises(DomainError):client.request('call',tool='affect_archive_v1',schema_hash=hashes['affect_archive_v1'],arguments={'item':item,'signature':'0'*64},timeout=10)

    def test_json_and_idempotent_archive(self):self.run_contract(True)
    def test_sse_and_schema_guard(self):self.run_contract(False)


if __name__=='__main__':unittest.main()
