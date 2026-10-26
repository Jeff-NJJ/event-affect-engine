"""Run optional real SDK contracts separately from standard-library regression."""
import argparse
import hashlib
import json
from pathlib import Path
import re
import subprocess

ROOT=Path(__file__).resolve().parents[1]

def main():
    p=argparse.ArgumentParser();p.add_argument('--sdk-python',required=True);p.add_argument('--report',required=True);args=p.parse_args()
    target=Path(args.report)
    if target.exists():raise SystemExit('Use a new report file')
    result=subprocess.run([args.sdk_python,'-B','integration_tests/test_mcp_ob.py','-q'],cwd=ROOT,
        capture_output=True,text=True,encoding='utf-8',errors='replace',timeout=90)
    meta=subprocess.check_output([args.sdk_python,'-c',
        "import importlib.metadata as m,json; print(json.dumps({k:m.version(k) for k in ('mcp','httpx','pydantic','pydantic-settings','anyio','starlette')}))"],text=True,encoding='utf-8')
    count=re.search(r'Ran (\d+) tests',result.stderr)
    report={'all_checks_passed':result.returncode==0,'exit_code':result.returncode,'test_count':int(count[1]) if count else None,
        'sdk_versions':json.loads(meta),'synthetic_only':True,'production_ob_contacted':False,
        'coverage':['actual official SDK JSON','actual official SDK SSE','schema hash change rejection',
            'idempotent OB-owned archive','invalid archive signature rejection','readonly bucket context'],
        'third_party_warnings':{'IncompleteFieldDefinitionWarning':'IncompleteFieldDefinitionWarning' in result.stderr,
            'ResourceWarning':'ResourceWarning' in result.stderr},
        'source_hashes':{n:hashlib.sha256((ROOT/n).read_bytes()).hexdigest() for n in (
            'ob_bridge/mcp_worker.py','ob_bridge/mcp_client.py','ob_extension/affect_bridge_tools.py','integration_tests/test_mcp_ob.py')}}
    if result.returncode:report['failure_output']=(result.stdout+result.stderr)[-12000:]
    target.write_text(json.dumps(report,ensure_ascii=False,indent=2)+'\n',encoding='utf-8')
    print(json.dumps({k:report[k] for k in ('all_checks_passed','test_count','third_party_warnings')}))
    if result.returncode:raise SystemExit(result.returncode)

if __name__=='__main__':main()
