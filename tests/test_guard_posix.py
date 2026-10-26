"""Behavioral tests in a real persistent POSIX shell; run automatically on Linux."""
import concurrent.futures
import os
import shutil
import subprocess
import tempfile
import unittest
from pathlib import Path

from tools.patch_workflows import guard_command

SHELL = os.environ.get("ENDOCRINE_TEST_SHELL") or shutil.which("sh")


@unittest.skipUnless(SHELL, "POSIX shell unavailable; run these scenarios on Linux/Android before cutover")
class PersistentShellRegressions(unittest.TestCase):
    def test_F11_success_and_skip_never_exit_parent_or_leave_owned_lock(self):
        with tempfile.TemporaryDirectory() as temp:
            root = str(Path(temp) / "directory with spaces")
            command = guard_command("slot", Path(root).as_posix(), 19800)
            result = subprocess.run([SHELL], input=command+"\nprintf '\nPARENT_ONE\n'\n"+command+"\nprintf '\nPARENT_TWO\n'\n",
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(result.stdout.count("WF_GUARD_GO"), 1)
            self.assertEqual(result.stdout.count("WF_GUARD_SKIP"), 1)
            self.assertIn("PARENT_TWO", result.stdout)
            self.assertFalse((Path(root)/"slot.lock").exists())

    def test_F11_repeated_allowed_runs_and_concurrent_cooldown_claims(self):
        with tempfile.TemporaryDirectory() as temp:
            root = str(Path(temp)/"guard")
            repeat = guard_command("repeat", Path(root).as_posix(), 0)
            result = subprocess.run([SHELL], input=repeat+"\n"+repeat+"\nprintf '\nPARENT_ALIVE\n'\n",
                                    text=True, capture_output=True, timeout=10)
            self.assertEqual(result.stdout.count("WF_GUARD_GO"), 2, result.stderr)
            self.assertIn("PARENT_ALIVE", result.stdout)
            command = guard_command("parallel", Path(root).as_posix(), 19800)
            with concurrent.futures.ThreadPoolExecutor(max_workers=8) as pool:
                rows = list(pool.map(lambda _:subprocess.run([SHELL,"-c",command],text=True,capture_output=True,timeout=10),range(12)))
            self.assertEqual(sum(r.stdout.count("WF_GUARD_GO") for r in rows), 1)
            self.assertFalse((Path(root)/"parallel.lock").exists())

    def test_F11_bad_cooldown_data_is_closed_and_one_time_claim_stays_claimed(self):
        with tempfile.TemporaryDirectory() as temp:
            root = Path(temp)/"guard"
            root.mkdir()
            (root/"bad.last").write_text("not-a-number",encoding="utf-8")
            command = guard_command("bad",root.as_posix(),19800)
            once = guard_command("once",root.as_posix())
            result = subprocess.run([SHELL],input=command+"\n"+once+"\n"+once+"\nprintf '\nPARENT_ALIVE\n'\n",
                                    text=True,capture_output=True,timeout=10)
            self.assertEqual(result.stdout.count("WF_GUARD_GO"), 1)
            self.assertEqual(result.stdout.count("WF_GUARD_SKIP"), 2)
            self.assertIn("PARENT_ALIVE",result.stdout)
            self.assertFalse((root/"bad.lock").exists())
            self.assertTrue((root/"once").is_dir())
