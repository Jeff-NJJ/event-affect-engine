"""Killable cloud HTTP worker; input/credentials arrive only via stdin."""
import json
import sys
import urllib.request

class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):return None

def main():
    try:
        raw=json.loads(sys.stdin.buffer.read(262145))
        req=urllib.request.Request(raw['endpoint'],data=json.dumps(raw['body'],ensure_ascii=False).encode(),
            headers={'Authorization':'Bearer '+raw['key'],'Content-Type':'application/json'})
        opener=urllib.request.build_opener(urllib.request.ProxyHandler({}),NoRedirect())
        with opener.open(req,timeout=raw['timeout']) as response:
            data=response.read(1048577)
            if len(data)>1048576:raise ValueError('response_limit')
        result={'ok':True,'data':json.loads(data).get('data')}
    except Exception:result={'ok':False,'error':'embedding_provider_unavailable'}
    sys.stdout.write(json.dumps(result))

if __name__=='__main__':main()
