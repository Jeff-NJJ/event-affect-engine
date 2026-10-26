import argparse
import json
import os
from pathlib import Path
import threading
import time

from endocrine.model import DomainError,canonical,identity
from endocrine.semantic import HTTPParagraphReranker
from .api import make_server
from .backend import MCPBackend,load_config
from .feed import AffectFeed,poll
from .mcp_client import MCPClient
from .service import BridgeService
from .store import ArchiveStore


def write_new(path,value):
    with Path(path).open('x',encoding='utf-8',newline='\n') as f:f.write(json.dumps(value,ensure_ascii=False,indent=2)+'\n')


def main():
    p=argparse.ArgumentParser();sub=p.add_subparsers(dest='command',required=True)
    d=sub.add_parser('discover');d.add_argument('--endpoint',default='http://127.0.0.1:18001/mcp')
    d.add_argument('--bearer-file');d.add_argument('--report',required=True)
    for name in ('serve','init','poll','dispatch','reconcile','consolidate'):
        x=sub.add_parser(name);x.add_argument('--config',required=True);x.add_argument('--db',required=True)
        if name in ('init','poll'):x.add_argument('--agent',required=True)
        if name=='init':x.add_argument('--start',choices=['current'],required=True)
        if name in ('dispatch','reconcile'):x.add_argument('--apply',action='store_true',required=True)
        if name=='serve':x.add_argument('--port',type=int,default=5020);x.add_argument('--worker',action='store_true')
        if name=='consolidate':
            x.add_argument('--start',required=True);x.add_argument('--end',required=True);x.add_argument('--report',required=True)
    args=p.parse_args()
    if args.command=='discover':
        headers={}
        if args.bearer_file:headers={'Authorization':'Bearer '+Path(args.bearer_file).read_text(encoding='utf-8').strip()}
        value=MCPClient(args.endpoint,headers=headers).request('discover',timeout=15)
        write_new(args.report,value);print('Discovery saved; tools called: 0; writes: 0');return
    config=load_config(args.config)
    backend=MCPBackend(config);store=ArchiveStore(args.db)
    reranker=None
    if config.get('backend')=='ob_structured_v1' and os.environ.get('CLASSIFIER_ENDPOINT'):
        options=json.loads(os.environ.get('CLASSIFIER_REQUEST_OPTIONS_JSON','{}'))
        reranker=HTTPParagraphReranker(os.environ['CLASSIFIER_ENDPOINT'],os.environ.get('CLASSIFIER_MODEL',''),
            os.environ.get('CLASSIFIER_KEY',''),request_options=options)
    service=BridgeService(backend,store,reranker=reranker)
    f=config.get('affect_feed',{})
    feed=AffectFeed(f['base'],f['token']) if f else None
    if args.command=='consolidate':write_new(args.report,service.consolidation(args.start,args.end));return
    if args.command in ('init','poll'):
        if feed is None:raise DomainError('affect_feed_not_configured',503)
        identity(args.agent,'agent_id')
        if args.command=='init':
            page=feed.page(args.agent,0);store.initialize_cursor(args.agent,page['stable_start_cursor'])
            print('Cursor starts after existing terminal prefix; pending events are preserved; no legacy replay');return
        print(canonical(poll(feed,store,args.agent,time.time())));return
    if args.command in ('dispatch','reconcile'):
        if config.get('archive_enabled') is not True:raise DomainError('ob_archive_disabled',503)
        print(canonical(service.dispatch_one() if args.command=='dispatch' else service.reconcile_one()));return
    token=os.environ.get('OB_BRIDGE_TOKEN','')
    server=make_server(service,token=token,feed=feed,archive_enabled=config.get('archive_enabled') is True,port=args.port)
    stop=threading.Event()
    if args.worker:
        if config.get('archive_enabled') is not True or feed is None:raise DomainError('archive_worker_not_configured',503)
        store.recover_interrupted(time.time())
        def worker():
            while not stop.is_set():
                for agent in sorted({r['agent_id'] for r in config['routes']}):
                    try:poll(feed,store,agent,time.time())
                    except Exception:pass # Cursor is preserved; no plaintext logs.
                try:service.dispatch_one();service.reconcile_one()
                except Exception:pass # Durable jobs remain recoverable after a transient worker failure.
                stop.wait(10)
        thread=threading.Thread(target=worker,daemon=True);thread.start()
    try:server.serve_forever()
    except KeyboardInterrupt:pass
    finally:stop.set();server.server_close()


if __name__=='__main__':
    try:main()
    except DomainError as exc:raise SystemExit(exc.code) from None
