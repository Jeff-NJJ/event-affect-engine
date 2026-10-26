"""Private IPC worker. Credentials travel on stdin, never command arguments/logs."""
import json
import os
import sys
import threading
import urllib.request
import urllib.error


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self,*args,**kwargs):
        return None  # Never forward a credential to a redirect target.


def main():
    raw=json.loads(sys.stdin.buffer.read(1048576))
    deadline=threading.Timer(raw['timeout'],lambda:os._exit(124))
    deadline.daemon=True;deadline.start()
    try:
        data=None if raw['data'] is None else json.dumps(raw['data'],ensure_ascii=False,allow_nan=False).encode()
        req=urllib.request.Request(raw['url'],data=data,headers=raw['headers'])
        with urllib.request.build_opener(NoRedirect()).open(req,timeout=raw['timeout']) as response:
            body=response.read(raw['max_bytes']+1)
        if len(body)>raw['max_bytes']:raise ValueError('response_size_limit')
        result={'ok':True,'value':json.loads(body)}
    except urllib.error.HTTPError as exc:result={'ok':False,'kind':'http','code':exc.code}
    except TimeoutError:result={'ok':False,'kind':'timeout'}
    except Exception:result={'ok':False,'kind':'invalid_or_unavailable'}
    finally:deadline.cancel()
    sys.stdout.buffer.write(json.dumps(result,ensure_ascii=False,allow_nan=False).encode())

if __name__=='__main__':main()
