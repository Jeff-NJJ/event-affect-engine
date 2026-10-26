import json,threading,time,unittest
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from endocrine.transport import request_json,TransportError

class TransportDeadlineTests(unittest.TestCase):
    def server(self,delay=0,trickle=False):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):
                if self.path=='/redirect':
                    self.send_response(302);self.send_header('Location','/target');self.end_headers();return
                time.sleep(delay)
                self.send_response(200);self.send_header('Content-Type','application/json');self.end_headers()
                try:
                    if trickle:
                        for b in b'{"connected":true}':self.wfile.write(bytes([b]));self.wfile.flush();time.sleep(.15)
                    else:self.wfile.write(b'{"connected":true}')
                except (BrokenPipeError,ConnectionResetError,ConnectionAbortedError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(server.server_close);self.addCleanup(server.shutdown)
        return 'http://127.0.0.1:'+str(server.server_port)

    def test_trickled_bytes_do_not_extend_total_deadline(self):
        url=self.server(trickle=True);start=time.monotonic()
        with self.assertRaises(TimeoutError):request_json(url,timeout=1)
        self.assertLess(time.monotonic()-start,2.5)

    def test_60_second_budget_can_wait_past_30_seconds(self):
        url=self.server(delay=31);start=time.monotonic()
        self.assertTrue(request_json(url,timeout=60)['connected'])
        self.assertGreaterEqual(time.monotonic()-start,31)
        self.assertLess(time.monotonic()-start,38)

    def test_credentials_are_not_forwarded_through_redirects(self):
        with self.assertRaisesRegex(TransportError,'provider_http_302'):
            request_json(self.server()+'/redirect',headers={'Authorization':'Bearer synthetic-only'},timeout=3)

if __name__=='__main__':unittest.main()
