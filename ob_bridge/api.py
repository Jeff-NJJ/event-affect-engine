import hmac
import json
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from urllib.parse import parse_qs,urlsplit
from endocrine.model import DomainError,canonical
from .feed import poll


def make_server(service,*,token,feed=None,archive_enabled=False,host='127.0.0.1',port=5020):
    if not isinstance(token,str) or not token:raise ValueError('Bridge token required')
    if host not in ('127.0.0.1','localhost','::1'):raise ValueError('Bridge must bind loopback')
    class Handler(BaseHTTPRequestHandler):
        def log_message(self,*args):pass
        def send(self,status,result):
            data=canonical(result).encode();self.send_response(status)
            self.send_header('Content-Type','application/json; charset=utf-8');self.send_header('Content-Length',str(len(data)))
            self.end_headers()
            try:self.wfile.write(data)
            except (BrokenPipeError,ConnectionResetError):pass
        def dispatch(self,method):
            try:
                if self.headers.get('Origin'):raise DomainError('browser_origin_not_allowed',403)
                if not hmac.compare_digest(self.headers.get('X-Token','').encode(),token.encode()):raise DomainError('forbidden',403)
                u=urlsplit(self.path)
                if method=='GET':
                    if u.path=='/bridge/health':
                        return self.send(200,{'success':True,'process_alive':True,'ob_connectivity':'not_probed',
                            'archive_enabled':archive_enabled,'messages_sent':0})
                    if u.path=='/bridge/consolidation':
                        q=parse_qs(u.query)
                        return self.send(200,service.consolidation(q.get('start',[''])[0],q.get('end',[''])[0]))
                    raise DomainError('not_found',404)
                try:
                    length=int(self.headers.get('Content-Length','0'))
                    if not 0<length<=65536:raise DomainError('invalid_body_size',413)
                    self.connection.settimeout(10)
                    body=json.loads(self.rfile.read(length))
                    if not isinstance(body,dict):raise ValueError()
                except (ValueError,TimeoutError):raise DomainError('invalid_json_body') from None
                if u.path=='/bridge/search':return self.send(200,service.search(body))
                if u.path in ('/bridge/poll','/bridge/archive'):
                    if not archive_enabled:raise DomainError('ob_archive_disabled',503)
                    if set(body)!={'agent_id'} or feed is None:raise DomainError('archive_requires_feed_agent')
                    # Never accept arbitrary raw text or a caller's invented
                    # conclusion: derive items from the authenticated affect feed.
                    result=poll(feed,service.store,body['agent_id'],service.clock())
                    if u.path=='/bridge/archive':result['dispatch']=service.dispatch_one()
                    return self.send(200,result)
                raise DomainError('not_found',404)
            except DomainError as exc:self.send(exc.status,{'success':False,'error':exc.code})
            except Exception:self.send(503,{'success':False,'error':'bridge_unavailable'})
        def do_GET(self):self.dispatch('GET')
        def do_POST(self):self.dispatch('POST')
    return ThreadingHTTPServer((host,port),Handler)
