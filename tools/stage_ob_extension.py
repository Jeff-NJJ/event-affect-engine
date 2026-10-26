"""Prepare new OB files for review. Never writes into the supplied OB tree."""
import argparse
import ast
import hashlib
import json
from pathlib import Path
import shutil

ROOT=Path(__file__).resolve().parents[1]
SERVER_SHA='6e304b853a8f4f1046fd88270ac1cb377576025c7eb1c22c64adcb9f86fd6189'

def main():
    p=argparse.ArgumentParser();p.add_argument('ob_src');p.add_argument('new_destination');args=p.parse_args()
    source=Path(args.ob_src).resolve();target=Path(args.new_destination).resolve()
    if target.exists() or target.is_relative_to(source):raise SystemExit('Use a new destination outside OB source')
    server=(source/'server.py').read_bytes()
    if hashlib.sha256(server).hexdigest()!=SERVER_SHA:raise SystemExit('OB server fingerprint changed; review before staging')
    content=server.decode('utf-8-sig')
    anchor='# MCP tools — thin registration wrappers'
    if content.count(anchor)!=1:raise SystemExit('Registration anchor mismatch')
    insertion='from affect_bridge_tools import register_from_environment as _register_affect_bridge\n_register_affect_bridge(mcp, bucket_mgr)\n\n'
    # Insert before the existing separator, after runtime initialization, without
    # importing server or starting it. This preserves every existing tool body.
    offset=content.rfind('# =============================================================',0,content.index(anchor))
    if offset<0:raise SystemExit('Registration section not found')
    patched=content[:offset]+insertion+content[offset:]
    ast.parse(patched)
    target.mkdir(parents=True)
    (target/'server.py').write_text(patched,encoding='utf-8',newline='\n')
    shutil.copyfile(ROOT/'ob_extension/affect_bridge_tools.py',target/'affect_bridge_tools.py')
    shutil.copyfile(ROOT/'ob_extension/affect_semantic_index.py',target/'affect_semantic_index.py')
    shutil.copyfile(ROOT/'ob_extension/affect_embedding_worker.py',target/'affect_embedding_worker.py')
    report={'source_sha256':SERVER_SHA,'candidate_sha256':hashlib.sha256((target/'server.py').read_bytes()).hexdigest(),
        'production_modified':False,'server_executed':False,'enablement':'OB_AFFECT_CONFIG required; disabled without private config'}
    (target/'staging_report.json').write_text(json.dumps(report,indent=2)+'\n',encoding='utf-8')
    print(json.dumps(report))

if __name__=='__main__':main()
