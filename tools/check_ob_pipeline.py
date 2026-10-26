"""Synthetic end-to-end MCP/bridge/cloud/affect/archive check, no real OB access."""
import argparse
import copy
from datetime import datetime,timezone
import hashlib
import importlib.util
import json
import logging
from pathlib import Path
import socket
import sys
import tempfile
import threading
import time

ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT));sys.path.insert(0,str(ROOT/'tools'))
from endocrine.api import make_server as affect_server
from endocrine.clock import FrozenClock
from endocrine.external_memory import OBMemoryClient
from endocrine.grounding import HTTPDialogueModel
from endocrine.integration import conclusion_feed
from endocrine.model import Config,digest,iso
from endocrine.semantic import HTTPParagraphReranker
from endocrine.service import Service
from endocrine.store import Repository
from ob_bridge.api import make_server as bridge_server
from ob_bridge.backend import MCPBackend
from ob_bridge.mcp_client import MCPClient
from ob_bridge.service import BridgeService
from ob_bridge.store import ArchiveStore
from build_ob_bindings import bindings


def main():
    p=argparse.ArgumentParser();p.add_argument('--report',required=True);p.add_argument('--endpoint',required=True)
    p.add_argument('--model',required=True);p.add_argument('--key-file',required=True);args=p.parse_args()
    target=Path(args.report)
    if target.exists():raise SystemExit('Report already exists')
    key=Path(args.key_file).read_text(encoding='utf-8').strip()
    import uvicorn
    from mcp.server.fastmcp import FastMCP
    logging.disable(logging.CRITICAL)
    spec=importlib.util.spec_from_file_location('affect_bridge_tools',ROOT/'ob_extension/affect_bridge_tools.py')
    extension=importlib.util.module_from_spec(spec);spec.loader.exec_module(extension)
    class Manager:
        def __init__(self):
            self.items={'privacy':{'content':'我同意你只告诉医生，并不想让同事知道这件私事。',
                'metadata':{'type':'dynamic','created':'2026-10-01T10:00:00+08:00','activation_count':7}},
                'other':{'content':'我最近换了一本笔记本。','metadata':{'type':'dynamic','created':'2026-10-01T10:00:00+08:00'}}}
        async def get(self,bid):return copy.deepcopy(self.items.get(bid))
    manager=Manager();before_ob=copy.deepcopy(manager.items)
    with tempfile.TemporaryDirectory() as temp:
        keyfile=Path(temp)/'archive-test.key';keyfile.write_bytes(b'synthetic-archive-secret-not-a-real-key-32')
        config={'archive_enabled':True,'archive_agents':['a'],'archive_db':str(Path(temp)/'ob_owned.sqlite'),
            'archive_signing_key_file':str(keyfile),
            'scopes':{'ap':{'bucket_ids':['privacy','other'],'source_utc_offset_minutes':480}}}
        s=socket.socket();s.bind(('127.0.0.1',0));port=s.getsockname()[1];s.close()
        mcp=FastMCP('OB synthetic pipeline',stateless_http=True,json_response=True)
        extension.register(mcp,manager,config)
        native=uvicorn.Server(uvicorn.Config(mcp.streamable_http_app(),host='127.0.0.1',port=port,log_level='critical',access_log=False))
        nt=threading.Thread(target=native.run,daemon=True);nt.start()
        deadline=time.monotonic()+10
        while not native.started and time.monotonic()<deadline:time.sleep(.02)
        if not native.started:raise RuntimeError('Fixture server failed')
        servers=[]
        try:
            endpoint='http://127.0.0.1:'+str(port)+'/mcp'
            discovered=MCPClient(endpoint).request('discover',timeout=10)
            backend=MCPBackend({'endpoint':endpoint,'backend':'ob_structured_v1','sdk_python':sys.executable,
                'routes':[{'agent_id':'a','actor_id':'p','ob_scope':'ap'}],'bindings':bindings(discovered['tools']),
                'archive_signing_key_file':str(keyfile)})
            bridge=BridgeService(backend,ArchiveStore(Path(temp)/'bridge.sqlite'),reranker=HTTPParagraphReranker(
                args.endpoint,args.model,key,request_options={'thinking':{'type':'disabled'}}))
            bs=bridge_server(bridge,token='synthetic-bridge-only',port=0)
            bt=threading.Thread(target=bs.serve_forever,daemon=True);bt.start();servers.append((bs,bt))
            now=time.time();service=Service(Repository(Path(temp)/'affect.sqlite',Config()),clock=FrozenClock(now))
            service.grounded.model=HTTPDialogueModel(args.endpoint,args.model,key,{'thinking':{'type':'disabled'}})
            service.grounded.external_memory=OBMemoryClient('http://127.0.0.1:'+str(bs.server_port),'synthetic-bridge-only')
            service.submit({'agent_id':'a','actor_id':'p','event_id':'u','conversation_id':'c',
                'source':'user_message','provenance':'observed_message','occurred_at':iso(now),'text':'继续谈一下之前的隐私边界。'})
            before=service.repo.export()
            reflected=service.grounded.reflect({'agent_id':'a','actor_id':'p','conversation_id':'c','source_event_id':'u',
                'hypothesis':'用户已经同意向所有同事公开这件私事。'})
            assertions={'ob_memory_found':any(r['source_kind']=='ob_memory' for r in reflected['evidence']),
                'contradiction_preserved':reflected['status']=='disputed','no_affect_mutation':before==service.repo.export(),
                'no_ob_activation':before_ob==manager.items,'external_evidence_labelled':reflected['external_memory']['status']=='available',
                'no_verified_user_events_from_summary':reflected['support_events']==0}
            service.submit({'agent_id':'a','actor_id':'p','event_id':'expect-add','source':'system','provenance':'operator_assertion',
                'occurred_at':iso(now),'event_type':'expect_add','payload':{'id':'x','expect':'PRIVATE-EXPECTATION',
                    'importance':.5,'deadline':iso(now+100),'mutual':False}})
            service.submit({'agent_id':'a','actor_id':'p','event_id':'expect-done','source':'system','provenance':'operator_assertion',
                'occurred_at':iso(now),'event_type':'expect_resolve','payload':{'id':'x','result':'unmet'}})
            item=conclusion_feed(service.repo,'a')['items'][0]
            bridge.store.queue(item,now);bridge.store.queue(item,now)
            original=backend.archive
            def lost(body,timeout):original(body,timeout);raise TimeoutError('committed but receipt lost')
            backend.archive=lost
            unknown=bridge.dispatch_one();backend.archive=original
            reconciled=bridge.reconcile_one()
            material=bridge.consolidation(iso(now-1),iso(now+1))
            assertions.update({'archive_lost_receipt_unknown':unknown['status']=='unknown',
                'authoritative_reconciliation':reconciled['status']=='delivered','archive_once':len(bridge.store.rows())==1,
                'consolidation_same_id':material['items'][0]['archive_id']==item['archive_id'],
                'no_raw_expectation_archived':'PRIVATE-EXPECTATION' not in json.dumps(material),
                'ordinary_ob_unchanged_after_archive':before_ob==manager.items})
            report={'synthetic_only':True,'real_cloud_used':True,'production_ob_contacted':False,'external_messages_sent':0,
                'assertions':assertions,'all_assertions_passed':all(assertions.values()),'reflection':reflected,
                'archive_reconciliation':reconciled,'sdk':'mcp==1.26.0','configured_model':args.model,
                'source_hashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in (
                    'endocrine/grounding.py','ob_bridge/service.py','ob_bridge/backend.py','ob_extension/affect_bridge_tools.py')}}
            target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
            print('Synthetic pipeline assertions:',sum(assertions.values()),'/',len(assertions))
            if not all(assertions.values()):raise SystemExit(1)
        finally:
            for server,t in servers:server.shutdown();server.server_close();t.join(3)
            native.should_exit=True;nt.join(5)


if __name__=='__main__':main()
