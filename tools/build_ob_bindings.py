"""Generate a disabled private-config template from observed tools/list schemas."""
import argparse
import json
from pathlib import Path
import sys
ROOT=Path(__file__).resolve().parents[1];sys.path.insert(0,str(ROOT))
from endocrine.model import digest

def bindings(tools):
    by_name={t['name']:t for t in tools}
    result={}
    for op,name,mapping in (
        ('context','affect_context_v1',{'scope_id':'scope_id','as_of':'as_of','cursor':'cursor'}),
        ('archive','affect_archive_v1',{'item':'item','signature':'signature'}),
        ('lookup','affect_archive_lookup_v1',{'archive_id':'archive_id'})):
        if name not in by_name:raise ValueError('New OB extension tool missing: '+name)
        result[op]={'tool':name,'schema_hash':digest(by_name[name]['inputSchema']),
                    'argument_map':mapping,'static_arguments':{},'result_pointer':''}
    return result

def main():
    p=argparse.ArgumentParser();p.add_argument('tools_report');p.add_argument('new_config');args=p.parse_args()
    data=json.loads(Path(args.tools_report).read_text(encoding='utf-8'))
    tools=data.get('tools',data.get('result',{}).get('tools',[]))
    config={'schema_version':1,'enabled':False,'archive_enabled':False,'backend':'ob_structured_v1',
        'archive_signing_key_file':'/ABS/PRIVATE/affect_archive.key',
        'endpoint':'http://127.0.0.1:18001/mcp','headers':{},'routes':[],'bindings':bindings(tools),
        'affect_feed':{'base':'http://127.0.0.1:5021','token':'SET_LOCALLY'}}
    with Path(args.new_config).open('x',encoding='utf-8') as f:f.write(json.dumps(config,ensure_ascii=False,indent=2)+'\n')
    print('Disabled binding template written; configure explicit scopes locally')

if __name__=='__main__':main()
