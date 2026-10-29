"""Turn one n8n execution into a Say-Do Diff tool log, then check the agent's reply.

Usage:
  # from a saved execution (GET /api/v1/executions/<id>?includeData=true)
  python3 n8n_to_log.py --execution execution.json --map node_map.json

  # or fetch it from your n8n instance (public API key in N8N_API_KEY)
  N8N_URL=https://n8n.example.com N8N_API_KEY=... python3 n8n_to_log.py --id 1234 --map node_map.json

It writes tool_log.json and reply.txt to --out (default: current folder) and runs saydo_diff on them
if the package is importable. Standard library only.
"""
import argparse
import json
import os
import sys
import urllib.request

# Let `python3 n8n_to_log.py` find the saydo_diff package when run from a clone of this repo.
_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_ROOT, "saydo_diff")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)


def fetch(exec_id: str) -> dict:
    url = os.environ["N8N_URL"].rstrip("/") + f"/api/v1/executions/{exec_id}?includeData=true"
    req = urllib.request.Request(url, headers={"X-N8N-API-KEY": os.environ["N8N_API_KEY"],
                                               "accept": "application/json"})
    with urllib.request.urlopen(req, timeout=30) as r:
        return json.load(r)


def dig(obj, path: str):
    """Read a dotted path like 'body.id' from a dict; None if missing."""
    for part in path.split(".") if path else []:
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def first_json(run: dict) -> dict:
    """First output item of a node run. Tool sub-nodes of an AI Agent write to ai_tool, not main."""
    data = run.get("data") or {}
    for key in ("main", "ai_tool"):
        branches = data.get(key) or []
        for branch in branches:
            for item in branch or []:
                if isinstance(item, dict) and isinstance(item.get("json"), dict):
                    return item["json"]
    return {}


def to_log(execution: dict, node_map: dict) -> list:
    run_data = dig(execution, "data.resultData.runData") or {}
    calls = []
    for node, spec in node_map.get("nodes", {}).items():
        for run in run_data.get(node, []):
            out = first_json(run)
            entry = {"tool": spec.get("tool", node), "action": spec["action"]}
            target = dig(out, spec["target"]) if spec.get("target") else spec.get("target_value")
            if target is not None:
                entry["target"] = target
            for field in ("id", "status", "http_status", "amount", "idempotency_key"):
                path = spec.get(field)
                val = dig(out, path) if path else None
                if val is not None:
                    entry[field] = val
            if run.get("error") or run.get("executionStatus") == "error":
                entry["error"] = (run.get("error") or {}).get("message", "node error")
            elif "status" not in entry:
                # n8n only says the node ran; without a provider status or id this stays below S3.
                entry["status"] = "done"
            calls.append(entry)
    return calls


def reply_text(execution: dict, node_map: dict) -> str:
    spec = node_map.get("reply", {})
    runs = (dig(execution, "data.resultData.runData") or {}).get(spec.get("node", ""), [])
    return str(dig(first_json(runs[-1]), spec.get("field", "output")) or "") if runs else ""


def main(argv=None) -> int:
    p = argparse.ArgumentParser(description=__doc__.splitlines()[0])
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--execution", help="saved execution JSON (includeData=true)")
    g.add_argument("--id", help="execution id to fetch from N8N_URL")
    p.add_argument("--map", required=True, help="node_map.json: which nodes are tool calls")
    p.add_argument("--out", default=".", help="folder for tool_log.json and reply.txt")
    a = p.parse_args(argv)

    if a.id:
        execution = fetch(a.id)
    else:
        with open(a.execution, encoding="utf-8") as f:
            execution = json.load(f)
    with open(a.map, encoding="utf-8") as f:
        node_map = json.load(f)
    calls, reply = to_log(execution, node_map), reply_text(execution, node_map)
    os.makedirs(a.out, exist_ok=True)
    with open(os.path.join(a.out, "tool_log.json"), "w", encoding="utf-8") as f:
        json.dump(calls, f, indent=2)
    with open(os.path.join(a.out, "reply.txt"), "w", encoding="utf-8") as f:
        f.write(reply)
    print(f"wrote {len(calls)} tool call(s) and the reply to {a.out}")
    try:
        from saydo_diff import diff
    except ImportError:
        print("saydo_diff not installed; run: python3 -m saydo_diff --message reply.txt --log tool_log.json")
        return 0
    report = diff(reply, calls)
    print(report.to_text())
    return 2 if report.stop_ship else (1 if report.gaps else 0)


if __name__ == "__main__":
    sys.exit(main())
