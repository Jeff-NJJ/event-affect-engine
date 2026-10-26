"""Run offline regression checks and write a compact reproducible report."""
import ast
import argparse
import json
import os
import platform
import re
import shutil
import sqlite3
import subprocess
import sys
import tempfile
import time
from datetime import datetime, timezone
from pathlib import Path


ROOT = Path(__file__).resolve().parents[1]


def main():
    parser=argparse.ArgumentParser()
    parser.add_argument('--timeout-seconds',type=int,default=120)
    parser.add_argument('--report',default='reports/verification.json')
    args=parser.parse_args()
    if not 30<=args.timeout_seconds<=1800:parser.error('test process budget must be 30..1800 seconds')
    env = dict(os.environ, PYTHONDONTWRITEBYTECODE="1", PYTHONUTF8="1")
    env.setdefault('OPERIT_TEST_PYTHON',sys.executable)
    checks = []

    def run(name, command):
        started = time.monotonic()
        result = subprocess.run(command, cwd=ROOT, env=env, capture_output=True,
                                text=True, encoding="utf-8", errors="replace", timeout=args.timeout_seconds)
        record = {"name": name, "status": "passed" if result.returncode == 0 else "failed",
                  "duration_seconds": round(time.monotonic() - started, 3), "exit_code": result.returncode}
        if name == "python_regression":
            match = re.search(r"Ran (\d+) tests?", result.stderr)
            record["test_count"] = int(match[1]) if match else None
            skipped = re.search(r"skipped=(\d+)", result.stderr)
            record["skipped_count"] = int(skipped[1]) if skipped else 0
            record["executed_count"] = (record["test_count"] or 0) - record["skipped_count"]
        checks.append(record)
        print(name, record["status"])
        if result.returncode:
            print(result.stdout)
            print(result.stderr, file=sys.stderr)
        return result

    python_files = sorted(ROOT.rglob("*.py"))
    for path in python_files:
        ast.parse(path.read_text(encoding="utf-8"), filename=str(path))
    checks.append({"name": "python_syntax", "status": "passed", "file_count": len(python_files)})
    run("python_regression", [sys.executable, "-B", "-m", "unittest", "discover", "-s", "tests", "-q"])
    node = shutil.which("node")
    if node:
        for name in ("endocrine_bridge.js", "endocrine_api.js", "daily_life.guarded.js"):
            run("javascript_syntax:" + name, [node, "--check", str(ROOT / "operit" / name)])
        with tempfile.TemporaryDirectory() as tmp:
            script = Path(tmp) / "generated_request.py"
            result = run("bridge_contract", [node, "tests/test_bridge.js", str(script)])
            if result.returncode == 0:
                ast.parse(script.read_text(encoding="utf-8"))
                checks.append({"name": "generated_python_syntax", "status": "passed"})
        run("daily_life_creation_graph", [node, "tests/test_daily_life.js"])
        run("expression_hook_syntax", [node, "--check", str(ROOT / "operit/expression_hook/main.js")])
        run("expression_hook_contract", [node, "tests/test_expression_hook.js"])
        run("grounding_bridge_contract", [node, "tests/test_grounding_bridge.js"])
        run('native_schedule_syntax',[node,'--check','operit/native_schedule.js'])
        run('native_client_contract',[node,'tests/test_native_client.js'])
        node_version = subprocess.check_output([node, "--version"], text=True).strip()
    else:
        node_version = None
        checks.append({"name": "javascript_checks", "status": "not_run", "reason": "Node unavailable"})
    report = {
        "generated_at_utc": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "python": platform.python_version(), "sqlite": sqlite3.sqlite_version, "node": node_version,
        "platform": platform.system(), "checks": checks,
        "all_checks_passed": all(c["status"] == "passed" for c in checks),
        "scope": "v5.7 offline regression: native message receipts and clocks, cloud-vector segmentation/cache/deadlines, actual preparedHistory prompt contract, workflow graph/idempotency and Shanghai calendar boundaries. SDK transport and real cloud/Android runtime evidence are separate reports.",
        "not_verified": ["statistical semantic accuracy or human psychological validity",
                         "production transport or installed APK runtime within this offline suite",
                         "external social message delivery", "v4-to-v3 inverse migration"],
    }
    report['test_process_budget_seconds']=args.timeout_seconds
    target = ROOT / args.report
    target.parent.mkdir(exist_ok=True)
    target.write_text(json.dumps(report, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    if not report["all_checks_passed"]:
        raise SystemExit(1)


if __name__ == "__main__":
    main()
