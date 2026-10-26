"""Generate reviewed Operit JSON guards using existing node schemas. Never installs."""
import argparse
import copy
import hashlib
import json
import shlex
from pathlib import Path

TYPE = "com.ai.assistance.operit.data.model."


def static(value):
    return {"__type": TYPE + "ParameterValue.StaticValue", "value": str(value)}


def reference(node):
    return {"__type": TYPE + "ParameterValue.NodeReference", "nodeId": node}


def guard_command(key, root, cooldown=None):
    """mkdir is the atomic claim; never release a one-time delivery claim automatically."""
    quoted = shlex.quote(str(root))
    marker = "printf 'WF_%s_%s' GUARD"
    if cooldown is None:
        return (f"( base={quoted}; umask 077; if ! mkdir -p \"$base\"; then {marker} SKIP; "
                f"elif mkdir \"$base/{key}\" 2>/dev/null; then {marker} GO; else {marker} SKIP; fi )")
    return (f"( base={quoted}; umask 077; if ! mkdir -p \"$base\"; then {marker} SKIP; exit 0; fi; "
            f"lock=\"$base/{key}.lock\"; if ! mkdir \"$lock\" 2>/dev/null; then {marker} SKIP; exit 0; fi; "
            "trap 'rmdir \"$lock\" 2>/dev/null' 0; trap 'exit 1' HUP INT TERM; "
            f"f=\"$base/{key}.last\"; now=$(date +%s); last=0; "
            "if [ -f \"$f\" ]; then last=$(cat \"$f\"); fi; "
            f"case \"$now\" in ''|*[!0-9]*) {marker} SKIP; exit 0;; esac; "
            f"case \"$last\" in ''|*[!0-9]*) {marker} SKIP; exit 0;; esac; "
            f"if [ \"$last\" -ne 0 ] && [ $((now-last)) -lt {int(cooldown)} ]; then {marker} SKIP; "
            f"elif printf '%s' \"$now\" > \"$f.tmp\" && mv \"$f.tmp\" \"$f\"; then {marker} GO; else {marker} SKIP; fi )")


def patch(workflow, root="/sdcard/Download/Operit/.delivery_guard_v4"):
    w = copy.deepcopy(workflow)
    trigger = next((n for n in w["nodes"] if n.get("triggerConfig", {}).get("schedule_type") in {"specific_time", "interval"}), None)
    if not trigger:
        raise ValueError("No supported scheduled trigger")
    config = trigger["triggerConfig"]
    key = hashlib.sha256((w["id"] + ":" + config.get("specific_time", "interval")).encode()).hexdigest()[:32]
    once = config["schedule_type"] == "specific_time"
    cmd = guard_command(key, root, None if once else 19800)
    if not once:
        old = next((n for n in w["nodes"] if n["id"] == "exec_guard_yjm01"), None)
        if old is None:
            raise ValueError("Interval template lacks known guard; manual integration required")
        old["actionConfig"]["command"] = static(cmd)
        extract = next(n for n in w["nodes"] if n["id"] == "extract_guard_yjm01")
        extract.update(expression="WF_GUARD_(GO|SKIP)", group=1, defaultValue="SKIP")
        condition = next(n for n in w["nodes"] if n["id"] == "cond_guard_yjm01")
        condition.update(operator="EQ", right=static("GO"))
        old["description"] = "原子领取与5.5小时冷却；异常默认跳过。锁残留需核查后清理。"
    else:
        if any(n["id"] == "guard_v4_exec" for n in w["nodes"]):
            raise ValueError("Already patched")
        outgoing = [c for c in w["connections"] if c["sourceNodeId"] == trigger["id"]]
        if not outgoing:
            raise ValueError("Trigger has no outgoing edge")
        w["nodes"].extend([
            {"__type": TYPE + "ExecuteNode", "id": "guard_v4_exec", "name": "防重·原子领取", "position": {"x": 0.0, "y": 100.0},
             "description": "发送前持久领取；失败关闭，领取后中断需人工核对，防止盲目重发。", "actionType": "super_admin:terminal",
             "actionConfig": {"command": static(cmd), "timeoutMs": static(15000)}},
            {"__type": TYPE + "ExtractNode", "id": "guard_v4_extract", "name": "提取防重结果", "position": {"x": 0.0, "y": 200.0},
             "source": reference("guard_v4_exec"), "expression": "WF_GUARD_(GO|SKIP)", "group": 1, "defaultValue": "SKIP"},
            {"__type": TYPE + "ConditionNode", "id": "guard_v4_condition", "name": "仅领取成功放行", "position": {"x": 0.0, "y": 300.0},
             "left": reference("guard_v4_extract"), "operator": "EQ", "right": static("GO")},
        ])
        for c in outgoing:
            c["sourceNodeId"] = "guard_v4_condition"
            c["condition"] = "true"
        for i, (a, b) in enumerate([(trigger["id"], "guard_v4_exec"), ("guard_v4_exec", "guard_v4_extract"), ("guard_v4_extract", "guard_v4_condition")]):
            w["connections"].append({"id": "guard-v4-link-" + str(i), "sourceNodeId": a, "targetNodeId": b, "condition": "on_success"})
    return w


def main():
    p = argparse.ArgumentParser()
    p.add_argument("source", type=Path)
    p.add_argument("output", type=Path)
    args = p.parse_args()
    args.output.mkdir(parents=True, exist_ok=True)
    count = 0
    for path in sorted(args.source.glob("*.json")):
        w = json.loads(path.read_text(encoding="utf-8"))
        if "nodes" not in w:
            continue
        result = patch(w)
        (args.output / path.name).write_text(json.dumps(result, ensure_ascii=False, indent=2), encoding="utf-8")
        count += 1
    print("Prepared guarded workflow JSON files:", count, "(not installed; original schedules preserved)")


if __name__ == "__main__":
    main()
