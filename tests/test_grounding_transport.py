"""Execute the actual generated bridge Python against isolated loopback servers."""
import contextlib
import io
import json
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path
import shutil
import subprocess
import tempfile
import threading
import unittest
from unittest.mock import patch


@unittest.skipUnless(shutil.which('node'),'Node required')
class GroundingTransportTests(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            script=Path(temp)/'request.py'
            subprocess.run([shutil.which('node'),str(Path(__file__).with_name('test_grounding_bridge.js')),str(script)],
                           check=True,capture_output=True,timeout=10)
            cls.script=script.read_text(encoding='utf-8')

    def execute(self,base):
        cfg=io.StringIO(json.dumps({'api_base':base,'token':'loopback-test-token'}))
        stdout=io.StringIO()
        with patch('builtins.open',return_value=cfg),contextlib.redirect_stdout(stdout):
            exec(compile(self.script,'generated_grounding.py','exec'),{})
        return json.loads(stdout.getvalue())

    def start(self,handler):
        server=ThreadingHTTPServer(('127.0.0.1',0),handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.addCleanup(lambda:(server.shutdown(),server.server_close(),thread.join()))
        return 'http://127.0.0.1:'+str(server.server_port)

    def test_real_redirect_does_not_forward_token_or_body(self):
        forwarded=[]
        class Target(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_GET(self):forwarded.append(self.headers.get('X-Token'));self.send_response(200);self.end_headers()
            do_POST=do_GET
        target=self.start(Target)
        class Redirect(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(307);self.send_header('Location',target+'/steal');self.end_headers()
        result=self.execute(self.start(Redirect))
        self.assertFalse(result['success']);self.assertEqual(forwarded,[])

    def test_generation_timeout_has_one_attempt_only(self):
        with patch('urllib.request.OpenerDirector.open',side_effect=TimeoutError('synthetic')) as call:
            result=self.execute('https://example.invalid')
        self.assertFalse(result['success']);self.assertEqual(call.call_count,1)
        self.assertEqual(call.call_args.kwargs['timeout'],160)

    def test_real_post_exact_unicode_and_no_proxy(self):
        requests=[]
        class Reply(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                requests.append((self.path,json.loads(self.rfile.read(int(self.headers['Content-Length']))),self.headers.get('X-Token')))
                result=json.dumps({'success':True,'status':'model_reviewed','reply':'我在听。🙂'},ensure_ascii=False).encode()
                self.send_response(200);self.send_header('Content-Length',str(len(result)));self.end_headers();self.wfile.write(result)
        with patch.dict('os.environ',{'HTTP_PROXY':'http://127.0.0.1:1','http_proxy':'http://127.0.0.1:1','NO_PROXY':'','no_proxy':''}):
            result=self.execute(self.start(Reply))
        self.assertEqual(result['reply'],'我在听。🙂');self.assertEqual(len(requests),1)
        self.assertEqual(requests[0][0],'/v2/expression/respond')
        self.assertEqual(requests[0][1],{'agent_id':'a','actor_id':'p','conversation_id':'c','source_event_id':'u'})
        self.assertEqual(requests[0][2],'loopback-test-token')
