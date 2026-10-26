"""Official pinned SDK in a killable process; credentials only arrive on stdin."""
import asyncio
from datetime import timedelta
import json
import logging
import sys


async def run(raw):
    import httpx
    from mcp import ClientSession
    from mcp.client.streamable_http import streamablehttp_client
    from jsonschema import Draft202012Validator
    from endocrine.model import digest
    # No redirect, proxy, automatic write retry, sampling or elicitation callback.
    def factory(headers=None,timeout=None,auth=None):
        return httpx.AsyncClient(headers=headers,timeout=timeout,auth=auth,follow_redirects=False,trust_env=False)
    async with streamablehttp_client(raw['endpoint'],headers=raw.get('headers',{}),
            timeout=timedelta(seconds=raw['timeout']),sse_read_timeout=timedelta(seconds=raw['timeout']),
            httpx_client_factory=factory) as (read,write,_):
        async with ClientSession(read,write,read_timeout_seconds=timedelta(seconds=raw['timeout'])) as session:
            initialized=await session.initialize()
            tools=[];cursor=None;seen=set()
            for _ in range(16):
                page=await session.list_tools(cursor=cursor)
                tools.extend(t.model_dump(by_alias=True,exclude_none=True) for t in page.tools)
                if len(tools)>128:raise ValueError('too_many_tools')
                cursor=page.nextCursor
                if cursor is None:break
                if cursor in seen:raise ValueError('cursor_cycle')
                seen.add(cursor)
            else:raise ValueError('too_many_pages')
            if raw['operation']=='discover':
                return {'server':initialized.serverInfo.model_dump(by_alias=True),
                    'protocol_version':initialized.protocolVersion,'tools':tools,
                    'tool_schema_hashes':{t['name']:digest(t['inputSchema']) for t in tools},'writes_performed':0}
            matching=[t for t in tools if t['name']==raw['tool']]
            if len(matching)!=1:raise ValueError('tool_missing_or_duplicate')
            schema=matching[0]['inputSchema']
            if digest(schema)!=raw['schema_hash']:raise ValueError('tool_schema_changed')
            Draft202012Validator(schema).validate(raw['arguments'])
            result=await session.call_tool(raw['tool'],raw['arguments'])
            if result.isError:raise ValueError('tool_reported_error')
            if result.structuredContent is not None:value=result.structuredContent
            else:
                blocks=[b.text for b in result.content if b.type=='text']
                if len(blocks)!=1:raise ValueError('unstructured_tool_result')
                value=json.loads(blocks[0])
            if len(json.dumps(value).encode())>262144:raise ValueError('tool_result_limit')
            return value


def main():
    logging.disable(logging.CRITICAL)
    try:
        raw=json.loads(sys.stdin.buffer.read(262145))
        value=asyncio.run(asyncio.wait_for(run(raw),raw['timeout']))
        output={'ok':True,'value':value}
    except ImportError:output={'ok':False,'error':'mcp_sdk_missing'}
    except Exception:output={'ok':False,'error':'mcp_call_failed_or_contract_changed'}
    sys.stdout.write(json.dumps(output,ensure_ascii=False))


if __name__=='__main__':main()
