"""Operator-reviewed tool mapping; no guesses about OB names or return fields."""
import copy
import hashlib
import hmac
import json
import time
from pathlib import Path
from endocrine.model import DomainError, canonical, identity
from .mcp_client import MCPClient


def pointer(value,path):
    if path=='':return value
    if not isinstance(path,str) or not path.startswith('/'):raise DomainError('invalid_ob_result_pointer',503)
    try:
        for part in path[1:].split('/'):
            part=part.replace('~1','/').replace('~0','~')
            value=value[int(part)] if isinstance(value,list) else value[part]
        return value
    except (KeyError,ValueError,IndexError,TypeError):raise DomainError('ob_result_mapping_mismatch',503) from None


class MCPBackend:
    def __init__(self,config):
        self.config=copy.deepcopy(config)
        self.client=MCPClient(config['endpoint'],headers=config.get('headers'),python=config.get('sdk_python'))

    def scope(self,request):
        rows=[r for r in self.config.get('routes',[]) if r['agent_id']==request['agent_id'] and r['actor_id']==request['actor_id']]
        if len(rows)!=1:raise DomainError('ob_scope_route_missing_or_ambiguous',503)
        return identity(rows[0]['ob_scope'],'ob_scope')

    def call(self,operation,values,timeout):
        binding=self.config.get('bindings',{}).get(operation)
        if not binding:raise DomainError('ob_tool_binding_missing',503)
        arguments=copy.deepcopy(binding.get('static_arguments',{}))
        for key,name in binding['argument_map'].items():
            if name not in values:raise DomainError('ob_argument_mapping_mismatch',503)
            if key in arguments:raise DomainError('ob_argument_mapping_collision',503)
            arguments[key]=values[name]
        result=self.client.request('call',tool=binding['tool'],schema_hash=binding['schema_hash'],
            arguments=arguments,timeout=timeout)
        return pointer(result,binding.get('result_pointer',''))

    def search(self,request,timeout=8):
        scope=self.scope(request)
        if self.config.get('backend')=='ob_semantic_v2':
            result=self.call('semantic',{**request,'scope_id':scope},timeout)
            if (not isinstance(result,dict) or result.get('scope_id')!=scope or result.get('memory_mutated') is not False
                or result.get('keyword_fallback') is not False or not isinstance(result.get('items'),list)
                or len(result['items'])>request['limit']):raise DomainError('invalid_semantic_ob_response',503)
            return scope,result['items']
        if self.config.get('backend')=='ob_structured_v1':
            deadline=time.monotonic()+timeout;cursor=0;records=[];version=None
            for _ in range(64):
                remaining=deadline-time.monotonic()
                if remaining<1:raise DomainError('ob_context_deadline',503)
                page=self.call('context',{'scope_id':scope,'as_of':request['as_of'],'cursor':cursor},remaining)
                if not isinstance(page,dict) or not isinstance(page.get('items'),list) or page.get('memory_mutated') is not False:
                    raise DomainError('ob_context_contract_invalid',503)
                if version is not None and page.get('scope_version')!=version:raise DomainError('ob_context_changed_during_pagination',409)
                version=page.get('scope_version')
                if not isinstance(version,str) or len(version)!=64:raise DomainError('invalid_ob_scope_version',503)
                records.extend(page['items'])
                if len(records)>1024:raise DomainError('ob_scope_context_limit',503)
                nxt=page.get('next_cursor')
                if nxt is None:
                    if page.get('total')!=len(records):raise DomainError('ob_context_incomplete',503)
                    return scope,records
                if type(nxt) is not int or nxt<=cursor:raise DomainError('invalid_ob_cursor',503)
                cursor=nxt
            raise DomainError('ob_context_page_limit',503)
        items=self.call('search',{**request,'scope_id':scope},timeout)
        if not isinstance(items,list) or len(items)>128:raise DomainError('ob_search_contract_invalid',503)
        mapping=self.config['bindings']['search']['item_map']
        required={'id','scope_id','text','kind','origin_system','known_at','valid_from','valid_to','source_refs','lineage','relevance'}
        if set(mapping)!=required:raise DomainError('ob_item_mapping_incomplete',503)
        return scope,[{k:pointer(item,path) for k,path in mapping.items()} for item in items]

    def archive(self,item,timeout=30):
        # Receipt must echo archive ID and fingerprint. A timeout remains unknown.
        key=Path(self.config['archive_signing_key_file']).read_bytes().strip()
        if len(key)<32:raise DomainError('invalid_archive_signing_key',503)
        signature=hmac.new(key,canonical(item).encode(),hashlib.sha256).hexdigest()
        return self.call('archive',{'item':item,'archive_id':item['archive_id'],'signature':signature},timeout)

    def lookup(self,archive_id,timeout=8):
        return self.call('lookup',{'archive_id':archive_id},timeout)


def load_config(path):
    try:
        data=Path(path).read_bytes()
        if len(data)>131072:raise ValueError()
        raw=json.loads(data)
        if not isinstance(raw,dict) or raw.get('schema_version')!=1:raise ValueError()
        # The example is intentionally disabled until real tools/list is reviewed.
        if raw.get('enabled') is not True:raise DomainError('ob_integration_disabled',503)
        if not isinstance(raw.get('routes'),list) or not isinstance(raw.get('bindings'),dict):raise ValueError()
        return raw
    except (OSError,ValueError,KeyError,TypeError):raise DomainError('invalid_ob_private_config',503) from None
