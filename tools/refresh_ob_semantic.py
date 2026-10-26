"""Refresh only the derived cloud-vector cache via the pinned MCP contract."""
import argparse
import json
import os
from pathlib import Path
import sys
import time
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from ob_bridge.backend import MCPBackend,load_config
from endocrine.model import iso

def main():
    p=argparse.ArgumentParser();p.add_argument('--config',required=True);args=p.parse_args()
    backend=MCPBackend(load_config(args.config))
    scopes=sorted({r['ob_scope'] for r in backend.config['routes']})
    for scope in scopes:
        result=backend.call('refresh',{'scope_id':scope,'as_of':iso(time.time())},timeout=120)
        if result.get('ordinary_buckets_written')!=0:raise ValueError('Unexpected source mutation')
        print(json.dumps({'success':True,**result}))

if __name__=='__main__':main()
