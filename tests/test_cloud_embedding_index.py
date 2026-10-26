import importlib.util
from pathlib import Path
import tempfile
import threading
import time
import subprocess
import os
from http.server import BaseHTTPRequestHandler,ThreadingHTTPServer
from unittest.mock import patch
import unittest

ROOT=Path(__file__).resolve().parents[1]
spec=importlib.util.spec_from_file_location('cloud_index',ROOT/'ob_extension/affect_semantic_index.py')
m=importlib.util.module_from_spec(spec);spec.loader.exec_module(m)


class CloudIndexTests(unittest.TestCase):
    def setUp(self):
        self.tmp=tempfile.TemporaryDirectory();self.addCleanup(self.tmp.cleanup);self.inputs=[]
        def embed(texts,timeout):
            self.inputs.extend(texts)
            return [[1 if '目标' in text else 0,0 if '目标' in text else 1]+[0]*62 for text in texts]
        self.index=m.CloudIndex({'endpoint':'https://example.invalid/embeddings','model':'synthetic','dimensions':64,
            'cache_db':str(Path(self.tmp.name)/'index.sqlite3')},provider=embed)

    def test_late_paragraph_segment_participates_and_full_text_retained(self):
        long='正文'*1300+'目标'
        self.index.prepare([{'id':'a','text':long},{'id':'b','text':'无关'}])
        result=self.index.search([{'id':'a','text':long},{'id':'b','text':'无关'}],'目标',1)
        self.assertEqual(result[0]['id'],'a');self.assertEqual(result[0]['text'],long)
        self.assertEqual(''.join(m.chunks(long)),long)

    def test_cache_reuse_and_content_change(self):
        self.index.prepare([{'id':'a','text':'原文'}]);count=len(self.inputs)
        self.index.prepare([{'id':'a','text':'原文'}]);self.assertEqual(count,len(self.inputs))
        self.index.prepare([{'id':'a','text':'修改'}]);self.assertGreater(len(self.inputs),count)

    def test_vectors_are_validated(self):
        for vector in ([0]*64,[float('nan')]*64,[1]*63,[True]*64):
            with self.assertRaises(ValueError):m.normalized(vector,64)

    def test_oversize_and_empty_are_explicit_errors(self):
        for text in ('',' '*10,'x'*8001):
            with self.assertRaises(ValueError):m.chunks(text)

    def test_lock_contention_respects_total_query_deadline(self):
        errors=[]
        def blocked():
            try:self.index.vectors(['x'],time.monotonic()+.05)
            except Exception as error:errors.append(error)
        self.index.lock.acquire()
        try:
            thread=threading.Thread(target=blocked);thread.start();thread.join(1)
            self.assertFalse(thread.is_alive());self.assertIsInstance(errors[0],TimeoutError)
        finally:self.index.lock.release()

    def test_trickle_http_is_killed_by_parent_deadline(self):
        class Handler(BaseHTTPRequestHandler):
            def log_message(self,*args):pass
            def do_POST(self):
                self.rfile.read(int(self.headers['Content-Length']))
                self.send_response(200);self.end_headers()
                try:
                    for _ in range(100):self.wfile.write(b' ');self.wfile.flush();time.sleep(.03)
                except (BrokenPipeError,ConnectionResetError):pass
        server=ThreadingHTTPServer(('127.0.0.1',0),Handler)
        thread=threading.Thread(target=server.serve_forever,daemon=True);thread.start()
        self.index.endpoint='http://127.0.0.1:'+str(server.server_port)
        self.index.config['key_environment']='SYNTHETIC_EMBED_KEY'
        started=time.monotonic()
        try:
            with patch.dict(os.environ,{'SYNTHETIC_EMBED_KEY':'synthetic-only'}):
                with self.assertRaises(subprocess.TimeoutExpired):self.index._embed(['x'],.3)
            self.assertLess(time.monotonic()-started,2)
        finally:server.shutdown();server.server_close();thread.join(2)
