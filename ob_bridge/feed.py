"""Pull only the versioned conclusion feed; cursor commits with local queue."""
from urllib.parse import urlencode
from endocrine.model import DomainError,identity
from endocrine.transport import request_json
from .contracts import local_or_https


class AffectFeed:
    def __init__(self,base,token):self.base,self.token=local_or_https(base).rstrip('/'),token

    def page(self,agent,after=0):
        result=request_json(self.base+'/v2/integration/changes?'+urlencode({'agent_id':agent,'after':after,'limit':100}),
            headers={'X-Token':self.token},timeout=8,max_bytes=262144)
        if (not isinstance(result,dict) or result.get('success') is not True or result.get('agent_id')!=agent
            or result.get('after')!=after or type(result.get('next_cursor')) is not int
            or result['next_cursor']<after or not isinstance(result.get('items'),list)):
            raise DomainError('invalid_affect_conclusion_feed',503)
        return result


def poll(feed,store,agent,now):
    identity(agent,'agent_id')
    after=store.cursor(agent)
    page=feed.page(agent,after)
    store.ingest(agent,after,page['next_cursor'],page['items'],now)
    return {'success':True,'agent_id':agent,'after':after,'next_cursor':page['next_cursor'],
        'queued_candidates':len(page['items']),'blocked_on_pending':page.get('blocked_on_pending',False)}
