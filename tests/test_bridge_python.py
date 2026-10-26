"""Execute generated client Python with mocked HTTP, never a real provider."""
import contextlib
import io
import json
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch


@unittest.skipUnless(shutil.which("node"), "Node required to generate bridge request")
class GeneratedPythonTransport(unittest.TestCase):
    @classmethod
    def setUpClass(cls):
        with tempfile.TemporaryDirectory() as temp:
            output = Path(temp)/"request.py"
            run = subprocess.run([shutil.which("node"),str(Path(__file__).with_name("test_bridge.js")),str(output)],capture_output=True,timeout=10)
            if run.returncode:
                raise AssertionError(run.stderr)
            cls.script = output.read_text(encoding="utf-8")

    def execute(self, responder):
        stdout = io.StringIO()
        cfg = json.dumps({"api_base":"https://example.invalid/affect", "token":"synthetic-test-only"})
        with patch("builtins.open",return_value=io.StringIO(cfg)), patch("urllib.request.OpenerDirector.open",side_effect=responder), patch("time.sleep"), contextlib.redirect_stdout(stdout):
            exec(compile(self.script,"generated_client.py","exec"),{})
        return json.loads(stdout.getvalue())

    def test_F03_lost_response_retries_same_body_and_real_unicode_bytes(self):
        requests = []
        def respond(request, **kw):
            requests.append(request.data)
            if len(requests) == 1:
                raise TimeoutError("synthetic response lost")
            return io.BytesIO(json.dumps({"success":True,"event_id":json.loads(request.data)["event_id"]}).encode())
        result = self.execute(respond)
        self.assertTrue(result["success"])
        self.assertEqual(requests[0],requests[1])
        body = json.loads(requests[0])
        self.assertEqual(body["event_id"],"operit:c:18542")
        self.assertEqual(body["agent_id"],"example_agent")
        self.assertIn("🙂 $(no_exec)",body["msg"])
        self.assertIn("\n第二行",body["msg"])

    def test_F03_exhausted_transport_reports_uncertainty_without_success(self):
        def respond(*args,**kw):
            raise TimeoutError("synthetic timeout")
        result = self.execute(respond)
        self.assertFalse(result["success"])
        self.assertEqual(result["error"],"transport_unavailable_delivery_unknown")
