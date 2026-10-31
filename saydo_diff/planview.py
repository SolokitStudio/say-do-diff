"""Plan -> done -> claim view: for each step of an approved plan, what the agent was allowed to do,
what the log shows it did, and what its reply claims. Built from a Report whose log came from an
importer that knows the plan (planlock today)."""
from __future__ import annotations

from .adapters.planlock import describe_constraints

STEP_LABEL = {"implemented": "IMPLEMENTED", "on_hold": "ON HOLD", "overridden": "OVERRIDDEN",
              "pending": "NOT RUN", "aborted": "ABORTED"}
PLAN_LABEL = {"completed": "completed", "on_hold": "on hold", "in_progress": "in progress", "aborted": "aborted"}


def _args(c: dict) -> str:
    t = c.get("target") or {}
    parts = [f"{k}={v}" for k, v in t.items()] if isinstance(t, dict) else [str(t)]
    if c.get("amount") is not None:
        parts.append(f"amount={c['amount']:g}")
    s = " ".join(parts) if parts else "(arguments not logged)"
    return s + (" [args from plan]" if c.get("args_source") == "plan" else "")


def build(report) -> list:
    """Structured view: one dict per plan."""
    imp = report.imported
    if imp is None or not imp.plans:
        return []
    by_i = {c["_i"]: c for c in report.calls}
    claim_steps = []          # (finding, {(plan, step)})
    for f in report.findings:
        keys = {(by_i[i]["step"]["plan"], by_i[i]["step"]["index"]) for i in f.calls
                if i in by_i and by_i[i].get("step")}
        claim_steps.append((f, keys))
    out = []
    for pi, p in enumerate(imp.plans):
        steps = []
        for s in p["steps"]:
            done = [c for c in report.calls if (c.get("step") or {}).get("plan") == pi
                    and c["step"]["index"] == s["index"]]
            claims = [f for f, keys in claim_steps if (pi, s["index"]) in keys and not f.claim.umbrella]
            planned = [{"tool": t, "constraints": describe_constraints(spec or {})}
                       for t, spec in (s.get("tools") or {}).items()]
            st = s["status"]
            if p["status"] == "aborted" and st == "pending":
                st = "aborted"
            steps.append({
                "index": s["index"], "name": s["name"], "description": s["description"], "status": st,
                "verdicts": s.get("verdicts", []), "reason": s.get("reason"), "justification": s.get("justification"),
                "verification": s.get("verification", []), "planned": planned,
                "done": [{"call": c["_i"], "tool": c.get("tool"), "grade": c["_grade"], "args": _args(c),
                          "note": c.get("note", ""), "superseded": bool(c.get("superseded"))} for c in done],
                "claims": [{"verb": f.claim.verb, "status": f.status, "grade": f.grade, "reason": f.reason,
                            "sentence": f.claim.sentence} for f in claims],
                "flag": _flag(st, done, claims),
            })
        out.append({"plan": pi, "objective": p["objective"], "plan_hash": p.get("plan_hash"),
                    "agreement_id": p.get("agreement_id"), "approved_by": p.get("approved_by"),
                    "approved_at": p.get("approved_at"), "status": p["status"],
                    "abort_reason": p.get("abort_reason"), "corrections": len(p.get("corrections") or []),
                    "steps": steps})

    off = [c for c in report.calls if not c.get("step")]
    if out:
        out[-1]["outside_plan"] = [{"call": c["_i"], "tool": c.get("tool"), "grade": c["_grade"],
                                    "route": (c.get("source") or {}).get("route"),
                                    "informational": bool(c.get("informational")), "note": c.get("note", "")}
                                   for c in off]
        out[-1]["unplaced_claims"] = [{"verb": f.claim.verb, "status": f.status, "grade": f.grade,
                                       "reason": f.reason, "sentence": f.claim.sentence}
                                      for f, keys in claim_steps if not keys]

    return out


def _flag(status: str, done: list, claims: list) -> str:
    claimed = bool(claims)
    gap = any(f.status == "gap" for f in claims)
    if status in ("implemented", "overridden") and not claimed:
        return "done, not mentioned in the reply"
    if status not in ("implemented", "overridden") and claimed:
        return "CLAIMED BUT NOT DONE" if gap or status != "implemented" else "claimed"
    if gap:
        return "claim stronger than evidence"
    if claimed:
        return "planned, done, claimed"
    return "not run, not claimed"


def render(report) -> str:
    views = build(report)
    if not views:
        return "(no plan in this log: the plan view needs a planlock log with a PLAN_APPROVED event)"
    lines = []
    for v in views:
        head = f"PLAN {v['plan'] + 1}: {v['objective']}"
        lines.append(head)
        meta = [f"status {PLAN_LABEL.get(v['status'], v['status'])}"]
        if v["plan_hash"]:
            meta.append(f"plan_hash {v['plan_hash'][:12]}")
        if v["approved_by"]:
            meta.append(f"approved by {v['approved_by']}")
        if v["approved_at"]:
            meta.append(f"at {v['approved_at']}")
        if v["corrections"]:
            meta.append(f"{v['corrections']} correction plan(s)")
        lines.append("  " + ", ".join(meta))
        if v.get("abort_reason"):
            lines.append(f"  aborted: {v['abort_reason']}")
        for s in v["steps"]:
            verdict = f" (audit {'/'.join(s['verdicts'])})" if s["verdicts"] else ""
            lines.append("")
            lines.append(f"  Step {s['index']} {s['name']}: {STEP_LABEL.get(s['status'], s['status'])}{verdict}"
                         f"  -> {s['flag']}")
            if s["reason"]:
                lines.append(f"    hold reason: {s['reason']}")
            if s["justification"]:
                lines.append(f"    override: {s['justification']}")
            lines.append(f"    plan:   {s['description']}" if s["description"] else "    plan:")
            for t in s["planned"]:
                lines.append(f"            {t['tool']}" + (f"  {t['constraints']}" if t["constraints"] else ""))
            if not s["done"]:
                lines.append("    done:   (no calls)")
            for n, d in enumerate(s["done"]):
                lines.append(f"    {'done:   ' if n == 0 else '        '}{d['grade']:<2} {d['tool']} {d['args']}"
                             + (f"  - {d['note']}" if d["grade"] in ("F", "S1") and d["note"] else ""))
            if not s["claims"]:
                lines.append("    claim:  (not mentioned)")
            for n, f in enumerate(s["claims"]):
                mark = "GAP" if f["status"] == "gap" else "ok"
                lines.append(f"    {'claim:  ' if n == 0 else '        '}[{mark}] {f['verb']} {f['grade']}  "
                             f"\"{f['sentence']}\"")
        if v.get("outside_plan"):
            lines.append("")
            lines.append("  Outside the plan:")
            for c in v["outside_plan"]:
                kind = " read-only" if c["informational"] else ""
                lines.append(f"    {c['grade']:<2} {c['tool']}{kind}  ({c['route']})"
                             + (f"  - {c['note']}" if c["grade"] in ("F", "S1") and c["note"] else ""))
        if v.get("unplaced_claims"):
            lines.append("")
            lines.append("  Claims not tied to a plan step:")
            for f in v["unplaced_claims"]:
                mark = "GAP" if f["status"] == "gap" else "ok"
                lines.append(f"    [{mark}] {f['verb']} {f['grade']}  {f['reason']}")
        lines.append("")
    return "\n".join(lines).rstrip()
