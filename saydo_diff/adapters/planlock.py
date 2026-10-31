"""Import a planlock session log (session_events.log) as a say-do-diff tool log.

planlock (https://github.com/JosephCurwin/planlock) is an MCP proxy that gates an agent's tool calls
behind an approved plan. It appends JSON lines to ``$LOG_ROOT/session_events.log``:

* records ``{"timestamp", "record_type", ...}``: txn_started, tool_call, tool_call_error,
  permission_denied, binding_violation, upstream_failure, step_not_executed, audit_verdict,
  hold_entered, hold_resolved, txn_committed, event_emitted, external_job_emitted, ...
* events ``{"timestamp", "event_name", "event_payload"}``: AGREEMENT_SUBMITTED/APPROVED/CONSUMED,
  PLAN_APPROVED (the plan: objective + steps with bound tools and argument constraints),
  STEP_IMPLEMENTED, SEQUENTIAL_PLAN_ON_HOLD, FAILURE_OVERRIDDEN_AND_PROMOTED, OVERRIDE_GRANTED,
  SEQUENTIAL_PLAN_ABORTED, CORRECTION_PLAN_APPROVED, BUGFIX_ATTEMPT_LOGGED.

The log names each tool but not its arguments or result. For ``PATCH /feature/implement/active``
planlock also writes a job packet ``<outbox>/<txn_id>.job.json`` whose ``job.ops`` list the calls
of that step, in order, with their arguments; this importer reads those for targets and amounts and
falls back to the plan's ``eq`` / single-value ``enum`` constraints when there is no packet.

Grades (see README "Works with planlock"):
  tool_call in a plan step whose audit_verdict is PASSED   S3  planlock checked the bound call
                                                               returned without isError
  other tool_call                                          S2  ran, no result or object id logged
  tool_call named by upstream_failure                      F   upstream returned isError
  tool_call_error                                          S1  the proxy raised; may have run
  permission_denied / binding_violation                    F   blocked before reaching upstream
"""
from __future__ import annotations

import json
import os

from ._common import READ_ONLY, infer_action, split_args, supersede

ACTIVE_ROUTE = "PATCH /feature/implement/active"
CALL_RECORDS = ("tool_call", "tool_call_error", "permission_denied", "binding_violation")


def looks_like(rows: list) -> bool:
    return any(isinstance(r, dict) and ("record_type" in r or "event_name" in r) for r in rows[:50])


def _read_rows(path: str) -> list:
    rows = []
    with open(path, encoding="utf-8") as fh:
        for n, line in enumerate(fh, 1):
            line = line.strip()
            if not line:
                continue
            try:
                obj = json.loads(line)
            except json.JSONDecodeError:
                rows.append({"_bad": line[:120], "_line": n})
                continue
            if isinstance(obj, dict):
                obj["_line"] = n
                rows.append(obj)
    return rows


def _outbox_dirs(log_path: str, rows: list, outbox: str | None) -> list:
    dirs = []
    if outbox:
        dirs.append(outbox)
    for r in rows:
        if r.get("record_type") == "external_job_emitted" and r.get("outbox_dir"):
            dirs.append(os.path.join(r["outbox_dir"], "outbox"))
    base = os.path.dirname(os.path.abspath(log_path))
    dirs += [os.path.join(base, "vm_io", "outbox"), os.path.join(base, "outbox")]
    seen, out = set(), []
    for d in dirs:
        if d not in seen and os.path.isdir(d):
            seen.add(d)
            out.append(d)
    return out


def _load_packet(dirs: list, txn_id: str):
    for d in dirs:
        p = os.path.join(d, f"{txn_id}.job.json")
        if os.path.isfile(p):
            try:
                with open(p, encoding="utf-8") as fh:
                    return json.load(fh), p
            except (OSError, json.JSONDecodeError):
                return None, p
    return None, None


def _plan_args(step: dict | None, tool: str) -> dict:
    """Argument values the plan pins exactly (eq, or an enum with one value)."""
    spec = ((step or {}).get("tools") or {}).get(tool) or {}
    out = {}
    for k, c in (spec.get("args") or {}).items():
        if isinstance(c, dict):
            if "eq" in c:
                out[k] = c["eq"]
            elif isinstance(c.get("enum"), list) and len(c["enum"]) == 1:
                out[k] = c["enum"][0]
    return out


def describe_constraints(spec: dict) -> str:
    parts = []
    for k, c in (spec.get("args") or {}).items():
        if not isinstance(c, dict):
            continue
        if "eq" in c:
            parts.append(f"{k} = {c['eq']!r}" if isinstance(c["eq"], str) else f"{k} = {c['eq']}")
        elif "enum" in c:
            parts.append(f"{k} in {c['enum']}")
        elif "min" in c or "max" in c:
            parts.append(f"{k} {c.get('min', '')}..{c.get('max', '')}")
        elif "max_length" in c:
            parts.append(f"len({k}) <= {c['max_length']}")
        elif c.get("any"):
            parts.append(f"{k} any")
    if spec.get("max_calls") is not None:
        parts.append(f"max {spec['max_calls']} call(s)")
    pin = spec.get("pin")
    if isinstance(pin, dict):
        parts.append(f"pin {pin.get('tool')}.{pin.get('field')} == {pin.get('eq')!r}")
    return ", ".join(parts)


def _read_profile(path: str | None) -> set:
    if not path:
        return set()
    with open(path, encoding="utf-8") as fh:
        prof = json.load(fh)
    return set(prof.get("investigation_read_only_tools") or [])


def import_planlock(path: str, mapping: dict | None = None, outbox: str | None = None,
                    profile: str | None = None, **_):
    rows = _read_rows(path)
    warnings = [f"line {r['_line']}: not JSON, skipped" for r in rows if "_bad" in r]
    rows = [r for r in rows if "_bad" not in r]
    read_only = _read_profile(profile)
    dirs = _outbox_dirs(path, rows, outbox)

    plans, plan, agreement = [], None, {}
    txns, calls, skipped = {}, [], {}

    def skip(kind):
        skipped[kind] = skipped.get(kind, 0) + 1

    for r in rows:
        ev, rt = r.get("event_name"), r.get("record_type")
        pl = r.get("event_payload") or {}
        if ev in ("AGREEMENT_SUBMITTED", "AGREEMENT_APPROVED", "AGREEMENT_CONSUMED"):
            if pl.get("agreement_id"):
                a = agreement.setdefault(pl["agreement_id"], {"agreement_id": pl["agreement_id"]})
                for k in ("plan_type", "plan_hash", "approved_by"):
                    if pl.get(k):
                        a[k] = pl[k]
                a["last"] = ev
                agreement["_latest"] = a
            continue
        if ev == "PLAN_APPROVED":
            p = pl.get("plan") or {}
            ag = agreement.get("_latest") or {}
            plan = {"objective": p.get("objective", ""), "plan_hash": ag.get("plan_hash"),
                    "agreement_id": ag.get("agreement_id"), "approved_by": ag.get("approved_by"),
                    "approved_at": r.get("timestamp"), "status": "in_progress", "current": 1,
                    "corrections": [], "line": r["_line"],
                    "steps": [{"index": i + 1, "name": s.get("name", f"step {i + 1}"),
                               "description": s.get("description", ""), "verification": s.get("verification") or [],
                               "tools": s.get("tools") or {}, "status": "pending", "txns": [], "verdicts": []}
                              for i, s in enumerate(p.get("steps") or [])]}
            plans.append(plan)
            continue
        if ev == "STEP_IMPLEMENTED" and plan:
            idx = int(pl.get("step_index") or plan["current"])
            if 1 <= idx <= len(plan["steps"]):
                plan["steps"][idx - 1]["status"] = "implemented"
            plan["current"] = idx + 1
            if idx >= len(plan["steps"]):
                plan["status"] = "completed"
            continue
        if ev == "SEQUENTIAL_PLAN_ON_HOLD" and plan:
            idx = int(pl.get("step_index") or plan["current"])
            if 1 <= idx <= len(plan["steps"]):
                plan["steps"][idx - 1]["status"] = "on_hold"
                plan["steps"][idx - 1]["reason"] = pl.get("reason") or pl.get("audit_report")
            plan["status"] = "on_hold"
            continue
        if ev == "FAILURE_OVERRIDDEN_AND_PROMOTED" and plan:
            idx = int(pl.get("resolved_step_index") or plan["current"])
            if 1 <= idx <= len(plan["steps"]):
                plan["steps"][idx - 1]["status"] = "overridden"
                plan["steps"][idx - 1]["justification"] = pl.get("justification")
            plan["current"] = int(pl.get("next_step_index") or idx + 1)
            plan["status"] = "completed" if plan["current"] > len(plan["steps"]) else "in_progress"
            continue
        if ev == "SEQUENTIAL_PLAN_ABORTED" and plan:
            plan["status"] = "aborted"
            plan["abort_reason"] = pl.get("reason")
            continue
        if ev == "CORRECTION_PLAN_APPROVED" and plan:
            plan["corrections"].append(pl.get("plan"))
            continue
        if ev:
            skip(f"event {ev}")
            continue

        tid = r.get("txn_id")
        if rt == "txn_started":
            t = txns[tid] = {"route": r.get("route"), "permission_class": r.get("permission_class"),
                             "allowed": (r.get("ack") or {}).get("allowed_tools") or [], "calls": [],
                             "verdict": None, "upstream": [], "plan": None, "step": None, "status_code": None,
                             "not_executed": False}
            if r.get("route") == ACTIVE_ROUTE and plan and plan["status"] in ("in_progress",):
                t["plan"], t["step"] = plan, plan["current"]
                if 1 <= plan["current"] <= len(plan["steps"]):
                    plan["steps"][plan["current"] - 1]["txns"].append(tid)
            if t["permission_class"] == "investigation":
                read_only.update(t["allowed"])
            continue
        if rt in CALL_RECORDS:
            if not tid:
                skip("planlock control call (" + str(r.get("tool")) + ")")
                continue
            t = txns.setdefault(tid, {"route": r.get("route"), "permission_class": None, "allowed": [],
                                      "calls": [], "verdict": None, "upstream": [], "plan": None, "step": None,
                                      "status_code": None, "not_executed": False})
            c = {"tool": r.get("tool"), "_rt": r, "_txn": tid}
            t["calls"].append(c)
            calls.append(c)
            continue
        if rt == "upstream_failure" and tid in txns:
            txns[tid]["upstream"].append(r.get("tool"))
        elif rt == "audit_verdict" and tid in txns:
            txns[tid]["verdict"] = r.get("verdict")
            tt = txns[tid]
            if tt["plan"] and tt["step"] and 1 <= tt["step"] <= len(tt["plan"]["steps"]):
                tt["plan"]["steps"][tt["step"] - 1]["verdicts"].append(r.get("verdict"))
        elif rt == "txn_committed" and tid in txns:
            txns[tid]["status_code"] = r.get("status_code")
        elif rt == "step_not_executed" and tid in txns:
            txns[tid]["not_executed"] = True
        elif rt == "hold_resolved" and plan and plan["status"] == "on_hold":
            plan["status"] = "in_progress"
        elif rt not in ("event_emitted", "external_job_emitted", "hold_entered", "external_job_emit_error",
                        "external_job_result", "txn_committed", "audit_verdict"):
            skip(f"record {rt}")

    # job packets: args for the op-bearing calls of each step transaction
    packets = 0
    for tid, t in txns.items():
        if t["route"] != ACTIVE_ROUTE:
            continue
        pkt, ppath = _load_packet(dirs, tid)
        if not pkt:
            continue
        job = pkt.get("job") or {}
        if t["plan"] and job.get("step_index") and job.get("step_index") != t["step"]:
            warnings.append(f"{tid}: job packet says step {job['step_index']}, log order says step {t['step']}")
            t["step"] = job["step_index"]
        ops = list(job.get("ops") or [])
        bearing = [c for c in t["calls"] if c["_rt"]["record_type"] in ("tool_call", "tool_call_error")
                   or (c["_rt"]["record_type"] == "binding_violation" and c["_rt"].get("violation") == "pin_mismatch")]
        if len(ops) != len(bearing) or any(o.get("op") != c["tool"] for o, c in zip(ops, bearing)):
            warnings.append(f"{tid}: job packet ops {[o.get('op') for o in ops]} do not line up with the "
                            f"logged calls {[c['tool'] for c in bearing]}; arguments not used")
            continue
        packets += 1
        for o, c in zip(ops, bearing):
            c["_args"] = {k: v for k, v in o.items() if k != "op"}
            c["_args_source"] = "job_packet"

    out = []
    for c in calls:
        r, t = c["_rt"], txns[c["_txn"]]
        tool, rt = c["tool"], r["record_type"]
        step = t["plan"]["steps"][t["step"] - 1] if t["plan"] and t["step"] and \
            1 <= t["step"] <= len(t["plan"]["steps"]) else None
        args, src = c.get("_args"), c.get("_args_source")
        if args is None:
            args = _plan_args(step, tool)
            src = "plan" if args else None
        target, amount = split_args(args)
        act = infer_action(tool, mapping)
        informational = act is READ_ONLY or tool in read_only
        e = {"tool": tool, "action": "" if act is READ_ONLY else act, "target": target}
        if amount is not None:
            e["amount"] = amount
        if not target:
            e["target_unknown"] = True
        if informational:
            e["informational"] = True
        if rt == "tool_call":
            if tool in t["upstream"] and not any(x["tool"] == tool and x["_rt"]["record_type"] == "tool_call_error"
                                                 for x in t["calls"]):
                t["upstream"].remove(tool)
                e.update(grade="F", status="failed", note="planlock upstream_failure: the upstream result had isError")
            elif step is not None and t["verdict"] == "PASSED":
                e.update(grade="S3", status="done",
                         note=f"step {t['step']} '{step['name']}' audit PASSED: planlock checked the bound call "
                              "returned without isError (no object id is logged)")
            else:
                e.update(grade="S2", status="done", note="ran through planlock; no result or object id is logged")
        elif rt == "tool_call_error":
            e.update(grade="S1", status="error", error=r.get("error"),
                     note=f"planlock tool_call_error: {r.get('error')}")
        elif rt == "permission_denied":
            e.update(grade="F", status="blocked", _supersedable=True,
                     note=f"planlock permission_denied: not allowed on {t['route']}")
        else:
            v = r.get("violation")
            if v == "pin_mismatch":
                v = f"pin_mismatch (expected {r.get('expected')!r}, got {r.get('actual')!r})"
            e.update(grade="F", status="blocked", _supersedable=True, note=f"planlock binding_violation: {v}")
            if r.get("violation") != "pin_mismatch" and c.get("_args") is None:
                # blocked by the argument check: planlock records neither the call nor its arguments
                e["target"], e["target_unknown"] = {}, True
                e.pop("amount", None)
                src = None
        e["source"] = {"format": "planlock", "line": r["_line"], "txn_id": c["_txn"], "route": t["route"]}
        if src:
            e["args_source"] = src
        if step is not None:
            e["step"] = {"plan": plans.index(t["plan"]), "index": t["step"], "name": step["name"]}
        out.append(e)
    supersede(out)

    for p in plans:
        p.pop("current", None)
    meta = {"format": "planlock", "rows": len(rows), "calls": len(out), "transactions": len(txns),
            "plans": len(plans), "job_packets": packets, "outbox_dirs": dirs, "skipped": skipped}
    if any(t["route"] == ACTIVE_ROUTE for t in txns.values()) and not packets:
        warnings.append("no planlock job packets found (looked in: " + (", ".join(dirs) or "nothing") +
                        "); targets come from the plan's eq constraints only. Pass --planlock-outbox DIR.")
    return out, plans, meta, warnings
