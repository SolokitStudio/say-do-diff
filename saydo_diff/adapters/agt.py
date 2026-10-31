"""Import Microsoft Agent Governance Toolkit (AGT) audit logs as a say-do-diff tool log.

https://github.com/microsoft/agent-governance-toolkit. Accepted inputs (auto-detected per row):

* agentmesh ``FileAuditSink`` JSONL (``SignedAuditEntry`` rows with content_hash, previous_hash,
  signature) and ``AuditLog.export()`` JSON (``{"entries": [AuditEntry...], "chain_root", ...}``),
  or a JSON list / JSONL of ``AuditEntry.model_dump()`` rows.
* ``AuditLog.export_cloudevents()`` output (CloudEvents 1.0 envelopes, type ``ai.agentmesh.*``).
* agent_os ``GovernanceAuditLogger`` JSONL (``JsonlFileBackend``), ``MCPGateway`` audit entries
  (tool_name / parameters / allowed) and ``BaseAgent.get_audit_log()`` rows (result_success).
* the TypeScript ``AuditLogger.exportJSON()`` and Go ``AuditLogger`` entries (agentId / agent_id,
  action, decision, hash, previousHash).

What each source can prove differs. AGT is a policy layer: most integrations log the *decision*
before the tool runs. Only rows that record an execution outcome (MAF middleware
``tool_invocation`` start/complete, ``agent_action``, BaseAgent ``result_success``) can back a
past-tense claim; an ``allow`` decision on its own grades S1 ("allowed, not shown to have run").
"""
from __future__ import annotations

import hashlib
import hmac
import json
import os
from datetime import datetime

from ._common import (READ_ONLY, find_amount, find_id, infer_action, parse_preview, scalar_fields, split_args,
                      supersede)

CE_TYPES = {
    "ai.agentmesh.tool.invoked": "tool_invocation",
    "ai.agentmesh.tool.blocked": "tool_blocked",
    "ai.agentmesh.policy.evaluation": "policy_evaluation",
    "ai.agentmesh.policy.violation": "policy_violation",
    "ai.agentmesh.trust.handshake": "trust_handshake",
    "ai.agentmesh.trust.score.updated": "trust_score_updated",
    "ai.agentmesh.agent.registered": "agent_registered",
    "ai.agentmesh.agent.verified": "agent_verified",
    "ai.agentmesh.audit.integrity.verified": "audit_integrity",
}
DECISION_WORDS = {"allow", "allowed", "deny", "denied", "block", "blocked", "audit", "warn", "warning",
                  "quarantine", "review", "escalate", "defer", "require_approval", "start", "complete"}
DENY = {"deny", "denied", "block", "blocked", "quarantine", "reject", "rejected"}
HOLD = {"review", "escalate", "defer", "require_approval", "pending", "requires_approval"}
DECISION_EVENTS = {"policy_evaluation", "policy_decision", "policy_violation", "governance_decision",
                   "policy_check", "mcp_tool_call"}
SKIP_EVENTS = {"agent_invocation", "trust_handshake", "trust_score_updated", "trust_change", "agent_registered",
               "agent_verified", "audit_integrity", "advisory_check", "on_flag_callback_error"}
ARG_KEYS = ("arguments", "args", "parameters", "params", "input", "tool_input", "kwargs")
RESULT_KEYS = ("result", "result_preview", "output", "response", "tool_output", "return_value")
SECRET_ENV = "AGT_AUDIT_SECRET"


# ----------------------------------------------------------------------------- loading
def _load(path: str):
    with open(path, encoding="utf-8") as fh:
        text = fh.read()
    s = text.strip()
    if not s:
        return [], "empty"
    try:
        data = json.loads(s)
    except json.JSONDecodeError:
        rows = []
        for n, line in enumerate(text.splitlines(), 1):
            if line.strip():
                try:
                    obj = json.loads(line)
                except json.JSONDecodeError:
                    rows.append({"_bad": line[:120], "_line": n})
                    continue
                if isinstance(obj, dict):
                    rows.append({**obj, "_line": n})
        return rows, "jsonl"
    if isinstance(data, dict) and isinstance(data.get("entries"), list):
        return [{**e, "_line": i + 1} for i, e in enumerate(data["entries"]) if isinstance(e, dict)], "export"
    if isinstance(data, dict):
        data = [data]
    return [{**e, "_line": i + 1} for i, e in enumerate(data) if isinstance(e, dict)], "json"


def kind_of(r: dict) -> str | None:
    if "specversion" in r and str(r.get("type", "")).startswith("ai.agentmesh"):
        return "cloudevent"
    if "event_type" in r and "agent_did" in r:
        return "signed" if "content_hash" in r else "agentmesh"
    if "tool_name" in r and "allowed" in r:
        return "mcp_gateway"
    if "request_id" in r and "result_success" in r:
        return "base_agent"
    if "event_type" in r and "agent_id" in r and "decision" in r:
        return "governance"
    if ("agentId" in r or "agent_id" in r) and "decision" in r and "action" in r:
        return "sdk"
    return None


def looks_like(rows: list) -> bool:
    return any(kind_of(r) for r in rows[:50] if isinstance(r, dict))


def _from_cloudevent(r: dict) -> dict:
    d = dict(r.get("data") or {})
    t = str(r.get("type", ""))
    out = {"entry_id": r.get("id"), "timestamp": r.get("time"), "agent_did": r.get("source"),
           "event_type": CE_TYPES.get(t, t[len("ai.agentmesh."):] if t.startswith("ai.agentmesh.") else t),
           "trace_id": r.get("traceid"), "session_id": r.get("sessionid"),
           "entry_hash": r.get("agentmeshentryhash"), "previous_hash": r.get("agentmeshprevioushash"),
           "_line": r.get("_line")}
    for k in ("action", "resource", "outcome", "policy_decision", "matched_rule", "policy_version",
              "arguments_hash", "approver_did", "issued_at", "completed_at"):
        out[k] = d.pop(k, None)
    out["data"] = d
    return out


# ----------------------------------------------------------------------------- integrity
def _ts_iso(v):
    """AuditEntry.compute_hash() hashes timestamp.isoformat(); exports carry str(datetime)."""
    if not isinstance(v, str):
        return v
    s = v
    if s.endswith("Z"):
        s = s[:-1] if ("+" in s[10:] or s[10:].count("-")) else s[:-1] + "+00:00"
    try:
        return datetime.fromisoformat(s).isoformat()
    except ValueError:
        return v


def _check_signed(rows, secret):
    prev = ""
    for r in rows:
        payload = {k: r.get(k) for k in ("entry_id", "timestamp", "event_type", "agent_did", "action", "resource",
                                         "target_did", "data", "outcome", "policy_decision", "matched_rule",
                                         "trace_id", "session_id", "previous_hash")}
        h = hashlib.sha256(json.dumps(payload, sort_keys=True, default=str).encode()).hexdigest()
        if r.get("previous_hash", "") != prev:
            return False, r["_line"], "previous_hash does not match the prior row (row removed or reordered)"
        if h != r.get("content_hash"):
            return False, r["_line"], "content_hash does not match the row (row edited)"
        if secret is not None:
            sig = hmac.new(secret, h.encode(), hashlib.sha256).hexdigest()
            if not hmac.compare_digest(sig, str(r.get("signature", ""))):
                return False, r["_line"], "HMAC signature does not verify with the given secret"
        prev = h
    return True, None, "content hashes and chain verify" + (", HMAC signatures verify" if secret is not None
                                                             else " (signatures not checked: no secret)")


def _check_entries(rows):
    prev = None
    for r in rows:
        if r.get("entry_hash") is None:
            return None, None, "no entry_hash"
        payload = {"entry_id": r.get("entry_id"), "timestamp": _ts_iso(r.get("timestamp")),
                   "event_type": r.get("event_type"), "agent_did": r.get("agent_did"), "action": r.get("action"),
                   "resource": r.get("resource"), "data": r.get("data") or {}, "outcome": r.get("outcome"),
                   "previous_hash": r.get("previous_hash")}
        h = hashlib.sha256(json.dumps(payload, sort_keys=True).encode()).hexdigest()
        if prev is not None and r.get("previous_hash") != prev:
            return False, r["_line"], "previous_hash does not match the prior entry"
        if h != r["entry_hash"]:
            return False, r["_line"], "entry_hash does not match the entry (entry edited)"
        prev = h
    return True, None, "entry hashes and chain verify"


def _check_links(rows, cur, prv, label):
    prev = None
    for r in rows:
        if prev is not None and r.get(prv) != prev:
            return False, r["_line"], f"{label}: previous hash does not match the prior entry"
        prev = r.get(cur)
    return True, None, f"{label}: hash links verify (content not recomputable from this export)"


def _check_ts(rows):
    prev = "0" * 64
    for r in rows:
        if r.get("previousHash") != prev:
            return False, r["_line"], "previousHash does not match the prior entry"
        payload = {"timestamp": r.get("timestamp"), "agentId": r.get("agentId"), "action": r.get("action"),
                   "decision": r.get("decision"), "previousHash": r.get("previousHash")}
        if r.get("skillAuditMetadata") is not None:
            payload["skillAuditMetadata"] = r["skillAuditMetadata"]
        h = hashlib.sha256(json.dumps(payload, separators=(",", ":"), ensure_ascii=False).encode()).hexdigest()
        if h != r.get("hash"):
            return False, r["_line"], "hash does not match the entry (entry edited)"
        prev = h
    return True, None, "hash chain verifies (TypeScript AuditLogger algorithm)"


def integrity(rows: list, kinds: list, secret: bytes | None):
    k = set(kinds)
    if k == {"signed"}:
        return _check_signed(rows, secret)
    if k == {"agentmesh"} and all("entry_hash" in r for r in rows):
        return _check_entries(rows)
    if k == {"cloudevent"}:
        return _check_links(rows, "agentmeshentryhash", "agentmeshprevioushash", "CloudEvents")
    if k == {"sdk"} and all("previousHash" in r for r in rows):
        return _check_ts(rows)
    if k == {"sdk"} and all("previous_hash" in r for r in rows):
        return _check_links(rows, "hash", "previous_hash", "Go AuditLogger")
    return None, None, "no hash chain to check for this input"


# ----------------------------------------------------------------------------- grading
def _first(d: dict, keys):
    for k in keys:
        if d.get(k) not in (None, "", {}):
            return d[k]
    return None


def _tool_of(e: dict) -> str | None:
    d = e.get("data") or {}
    t = _first(d, ("tool", "tool_name", "function", "function_name", "name"))
    if t:
        return str(t)
    if e.get("resource"):
        return str(e["resource"])
    a = e.get("action")
    if a and str(a).lower() not in DECISION_WORDS and str(a) != "mcp_tool_call":
        return str(a)
    return None


def _decision(e: dict) -> str:
    for v in (e.get("policy_decision"), e.get("outcome"), e.get("action"), (e.get("data") or {}).get("decision")):
        if v and str(v).lower() in DECISION_WORDS | DENY | HOLD:
            return str(v).lower()
    return str(e.get("outcome") or "").lower()


class _Builder:
    def __init__(self, mapping):
        self.mapping, self.calls, self.open, self.skipped = mapping, [], {}, {}

    def skip(self, why):
        self.skipped[why] = self.skipped.get(why, 0) + 1

    def new(self, tool, args, line, kind, extra=None):
        act = infer_action(tool, self.mapping)
        target, amount = split_args(args if isinstance(args, dict) else {})
        c = {"tool": tool, "action": "" if act is READ_ONLY else act, "target": target,
             "source": {"format": "agt", "kind": kind, "line": line, **(extra or {})}}
        if amount is not None:
            c["amount"] = amount
        if not target:
            c["target_unknown"] = True
        if act is READ_ONLY:
            c["informational"] = True
        self.calls.append(c)
        return c

    @staticmethod
    def result(c, res):
        obj = parse_preview(res)
        text = res if isinstance(res, str) else json.dumps(res, default=str) if res is not None else ""
        oid = find_id(obj, text)
        extra = scalar_fields(obj)
        if extra:
            tgt = dict(c.get("target") or {})
            for k, v in extra.items():
                tgt.setdefault(f"result.{k}", v)
            c["target"] = tgt
            c.pop("target_unknown", None)
        amt = find_amount(obj)
        if amt is not None and "amount" not in c:
            c["amount"] = amt
        if oid:
            c["id"] = oid
        return oid

    def executed(self, c, outcome, res, err=None):
        o = (outcome or "").lower()
        if o in ("failure", "failed"):
            c.update(grade="F", status="failed", note=f"AGT outcome {o}" + (f": {err}" if err else ""))
        elif o == "error":
            c.update(grade="S1", status="error", error=err or True, note="AGT outcome error" + (f": {err}" if err else ""))
        elif o in DENY:
            c.update(grade="F", status="blocked", _supersedable=True, note=f"AGT outcome {o}")
        else:
            oid = self.result(c, res) if res is not None else None
            c.update(grade="S3" if oid else "S2", status="done",
                     note="AGT recorded the completed call" + (f" with result id {oid}" if oid else
                                                               ", no object id in the result"))

    def decision(self, c, dec, rule=None, reason=None):
        why = ", ".join(x for x in (f"rule {rule}" if rule else "", reason or "") if x)
        if dec in DENY:
            c.update(grade="F", status="blocked", _supersedable=True,
                     note=f"AGT policy {dec}" + (f" ({why})" if why else ""))
        elif dec in HOLD:
            c.update(grade="S1", status="pending", _supersedable=True,
                     note=f"AGT policy {dec}: held for review, not shown to have run")
        else:
            c.update(grade="S1", status="attempted", _supersedable=True,
                     note=f"AGT policy {dec or 'allow'}: a decision before the call; the log does not show it ran")

    def entry(self, e, kind):
        et = str(e.get("event_type") or "")
        d = e.get("data") or {}
        line = e.get("_line")
        extra = {k: e[k] for k in ("entry_id", "trace_id", "session_id") if e.get(k)}
        if et in SKIP_EVENTS:
            return self.skip(f"event {et}")
        tool = _tool_of(e)
        if not tool:
            return self.skip(f"event {et} without a tool")
        args = _first(d, ARG_KEYS)
        res = _first(d, RESULT_KEYS)
        act = str(e.get("action") or "").lower()
        if et == "tool_invocation" and act == "start":
            c = self.new(tool, args, line, kind, extra)
            c.update(grade="S1", status="attempted",
                     note="AGT logged the tool start but no completion (the tool raised or the run stopped)")
            self.open.setdefault((tool, e.get("trace_id"), e.get("session_id")), []).append(c)
            return
        if et == "tool_invocation" and act == "complete":
            q = self.open.get((tool, e.get("trace_id"), e.get("session_id"))) or []
            c = q.pop(0) if q else self.new(tool, args, line, kind, extra)
            c["source"]["complete_line"] = line
            return self.executed(c, e.get("outcome"), res, d.get("error"))
        if et == "tool_blocked":
            c = self.new(tool, args, line, kind, extra)
            return self.decision(c, "denied", e.get("matched_rule"), d.get("reason"))
        if et in DECISION_EVENTS:
            c = self.new(tool, args if isinstance(args, dict) else None, line, kind, extra)
            return self.decision(c, _decision(e), e.get("matched_rule") or d.get("rule"), d.get("reason"))
        # agent_action, tool_invocation with a custom action, and integration-specific event types
        c = self.new(tool, args, line, kind, extra)
        self.executed(c, e.get("outcome"), res, d.get("error"))


def import_agt(path: str, mapping: dict | None = None, secret_env: str = SECRET_ENV, **_):
    raw, container = _load(path)
    warnings = [f"line {r['_line']}: not JSON, skipped" for r in raw if "_bad" in r]
    raw = [r for r in raw if "_bad" not in r]
    kinds = [kind_of(r) for r in raw]
    unknown = sum(1 for k in kinds if k is None)
    if unknown:
        warnings.append(f"{unknown} row(s) are not a known AGT audit shape and were skipped")
    rows = [r for r, k in zip(raw, kinds) if k]
    kinds = [k for k in kinds if k]
    secret = os.environ.get(secret_env)
    ok, bad_line, detail = integrity(rows, kinds, secret.encode() if secret else None)
    if ok is False:
        warnings.append(f"AGT audit chain BROKEN at entry {bad_line}: {detail}")

    b = _Builder(mapping)
    for r, k in zip(rows, kinds):
        if k in ("signed", "agentmesh"):
            b.entry(r, k)
        elif k == "cloudevent":
            b.entry(_from_cloudevent(r), k)
        elif k == "mcp_gateway":
            c = b.new(str(r["tool_name"]), r.get("parameters"), r["_line"], k)
            st = str(r.get("approval_status") or "").lower()
            # MCPGateway logs allowed=False with approval_status "pending" while it waits for a human
            b.decision(c, "review" if st == "pending" else
                       "denied" if not r.get("allowed") or st == "denied" else "allow", reason=r.get("reason"))
        elif k == "base_agent":
            c = b.new(str(r.get("action")), None, r["_line"], k, {"request_id": r.get("request_id")})
            if r.get("params_keys"):
                c["note_args"] = r["params_keys"]
            dec = str(r.get("decision") or "").lower()
            if dec in DENY:
                b.decision(c, dec)
            elif dec in HOLD:
                b.decision(c, dec)
            elif r.get("result_success") is True:
                b.executed(c, "success", None)
            elif r.get("result_success") is False:
                b.executed(c, "failure", None, r.get("error"))
            else:
                b.executed(c, "error", None, r.get("error") or "no result recorded")
        elif k == "governance":
            md = r.get("metadata") or {}
            tool = str(_first(md, ("tool", "tool_name")) or r.get("action") or "")
            if not tool:
                b.skip("governance_decision without a tool")
                continue
            c = b.new(tool, _first(md, ARG_KEYS), r["_line"], k)
            b.decision(c, str(r.get("decision") or "").lower(), reason=r.get("reason"))
        elif k == "sdk":
            c = b.new(str(r.get("action")), None, r["_line"], k,
                      {"agent": r.get("agentId") or r.get("agent_id")})
            b.decision(c, str(r.get("decision") or "").lower())
    supersede(b.calls)
    for c in b.calls:
        c.pop("note_args", None)
    meta = {"format": "agt", "container": container, "rows": len(raw), "calls": len(b.calls),
            "shapes": sorted(set(kinds)), "skipped": b.skipped,
            "integrity": {"ok": ok, "line": bad_line, "detail": detail}}
    if b.calls and not any(c.get("status") in ("done", "failed", "error") for c in b.calls):
        warnings.append("this AGT log records policy decisions only, not tool results: allowed calls grade S1, "
                        "so past-tense claims will show as gaps. Log tool outcomes (e.g. the MAF middleware's "
                        "tool_invocation start/complete) to back them.")
    return b.calls, [], meta, warnings
