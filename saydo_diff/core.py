"""Say-Do Diff: compare what an agent SAYS it did with what its tool log shows it DID.

Standard library only. Read-only: this module reports gaps, it never edits a reply.

Evidence grades (lowest to highest, plus failed):
  S0  no tool call recorded for the claim
  S1  call made, but error / timeout / unknown result
  S2  accepted or queued (HTTP 202, "pending", "processing"), or "done" with no object id
  S3  done, with an object id (message id, event id, charge id)
  S4  verified afterwards (delivered webhook, read-back)
  F   provider reported failure (bounce, 4xx/5xx, payment_failed)
"""
from __future__ import annotations

import json
import re
from dataclasses import dataclass, field, asdict

RANK = {"F": -1, "S0": 0, "S1": 1, "S2": 2, "S3": 3, "S4": 4}

# past-tense / participle form -> (action, needs_grade)
VERBS = {
    "send": ["sent", "emailed", "mailed", "forwarded", "texted", "messaged", "notified", "replied", "submitted",
             "invited", "shared"],
    "create": ["created", "added", "opened", "logged", "filed", "registered", "saved", "uploaded", "generated"],
    "update": ["updated", "changed", "edited", "modified", "marked", "moved", "renamed", "assigned", "closed",
               "resolved", "archived", "merged", "approved", "enabled", "disabled"],
    "delete": ["deleted", "removed", "erased", "purged", "wiped"],
    "book": ["booked", "scheduled", "reserved", "rescheduled"],
    "pay": ["paid", "refunded", "charged", "transferred", "reimbursed", "credited", "wired", "invoiced"],
    "post": ["posted", "published", "tweeted", "deployed", "released"],
    "cancel": ["canceled", "cancelled", "unsubscribed", "revoked", "voided"],
}
VERIFIED_WORDS = {"delivered", "confirmed", "verified", "settled"}
FORM_TO_ACTION = {form: action for action, forms in VERBS.items() for form in forms}
FORM_TO_ACTION.update({"delivered": "send", "settled": "pay"})

UMBRELLA = ["all set", "all done", "taken care of", "took care of", "good to go", "squared away",
            "everything is done", "everything is complete", "everything is in place", "you're set",
            "you are set", "wrapped up"]
BLOCKERS = {"will", "i'll", "we'll", "shall", "going", "gonna", "plan", "planning", "about", "can", "could",
            "should", "would", "might", "may", "must", "let", "want", "ready", "need", "needs", "if", "unless",
            "once", "when", "whether", "try", "trying", "not", "never", "didn't", "haven't", "hasn't",
            "wasn't", "weren't", "isn't", "aren't", "couldn't", "won't", "yet", "you", "they", "he", "she"}
NUMBER_WORDS = {"one": 1, "two": 2, "three": 3, "four": 4, "five": 5, "six": 6, "seven": 7, "eight": 8,
                "nine": 9, "ten": 10, "both": 2}

FAILED = {"failed", "failure", "error", "bounced", "bounce", "dropped", "rejected", "declined",
          "payment_failed", "canceled_by_provider"}
ATTEMPTED = {"timeout", "timed_out", "unknown", "attempted", "no_response"}
ACCEPTED = {"accepted", "queued", "pending", "processing", "scheduled_send", "in_progress"}
DONE = {"done", "ok", "success", "succeeded", "sent", "created", "completed", "booked", "paid"}
VERIFIED = {"verified", "delivered", "confirmed", "settled", "read_back"}

EMAIL_RE = re.compile(r"[\w.+-]+@[\w-]+(?:\.[\w-]+)+")
MONEY_RE = re.compile(r"[$€£]\s?(\d[\d,]*(?:\.\d+)?)")
ID_RE = re.compile(r"(?<![$\w.])#?(\d{3,})(?!\w|\.\d)")
QUOTED_RE = re.compile(r"[\"“']([^\"”']{2,60})[\"”']")
COUNT_RE = re.compile(r"\b(?:all\s+)?(\d+|" + "|".join(NUMBER_WORDS) + r")\s+(?:of\s+the\s+)?[a-z]+s\b",
                      re.I)
WORD_RE = re.compile(r"[A-Za-z']+|\u2705")


@dataclass
class Claim:
    sentence: str
    verb: str
    action: str
    needs: str                      # minimum grade the wording requires
    targets: list = field(default_factory=list)
    amount: float | None = None
    count: int | None = None
    umbrella: bool = False


@dataclass
class Finding:
    claim: Claim
    status: str                     # "ok" or "gap"
    grade: str                      # best grade found for this claim
    reason: str
    calls: list = field(default_factory=list)

    def to_dict(self):
        d = asdict(self)
        return d


# ----------------------------------------------------------------------------- tool log
def grade_call(call: dict) -> str:
    """Grade one tool-log entry. An explicit "grade" field wins."""
    g = str(call.get("grade", "")).upper()
    if g in RANK:
        return g
    status = str(call.get("status", "")).lower().strip()
    http = call.get("http_status")
    has_id = bool(call.get("id") or call.get("object_id"))
    if status in FAILED or (isinstance(http, int) and http >= 400):
        return "F"
    if call.get("error") or status in ATTEMPTED:
        return "S1"
    if status in VERIFIED or call.get("verified") is True:
        return "S4"
    if status in ACCEPTED or http == 202:
        return "S2"
    if status in DONE or (isinstance(http, int) and 200 <= http < 300):
        return "S3" if has_id else "S2"
    return "S1"


def load_log(source) -> list:
    """Accept a path, a JSON string, a list, or {"calls": [...]}. JSONL is accepted too."""
    if isinstance(source, list):
        data = source
    elif isinstance(source, dict):
        data = source.get("calls", [])
    else:
        text = source
        if not str(source).lstrip().startswith(("[", "{")):
            with open(source, encoding="utf-8") as fh:
                text = fh.read()
        text = text.strip()
        try:
            data = json.loads(text) if text else []
        except json.JSONDecodeError:
            data = [json.loads(line) for line in text.splitlines() if line.strip()]
        if isinstance(data, dict):
            data = data.get("calls", [])
    calls = []
    for i, c in enumerate(data):
        c = dict(c)
        c.setdefault("action", "")
        c["_grade"] = grade_call(c)
        c["_i"] = i
        calls.append(c)
    return calls


def _target_text(call: dict) -> str:
    t = call.get("target", "")
    if isinstance(t, dict):
        t = " ".join(str(v) for v in t.values())
    return f"{t} {call.get('id', '')} {call.get('object_id', '')}".lower()


# ----------------------------------------------------------------------------- claims
def _sentences(text: str) -> list:
    parts = re.split(r"(?<=[.!?])\s+|\n+", text.strip())
    return [p.strip() for p in parts if p.strip()]


def _num(tok: str) -> int:
    return int(tok) if tok.isdigit() else NUMBER_WORDS[tok.lower()]


def extract_claims(text: str) -> list:
    """Find first-person/passive completed-action claims, one per verb occurrence."""
    claims = []
    for sent in _sentences(text):
        low = sent.lower()
        words = [m for m in WORD_RE.finditer(sent)]
        hits = [(i, m) for i, m in enumerate(words) if m.group().lower() in FORM_TO_ACTION]
        for n, (i, m) in enumerate(hits):
            # clause = from the previous verb (or sentence start) to the next verb
            start_word = hits[n - 1][0] + 1 if n else 0
            prefix = [w.group().lower() for w in words[start_word:i]][-4:]
            if any(p in BLOCKERS for p in prefix):
                continue
            end = hits[n + 1][1].start() if n + 1 < len(hits) else len(sent)
            clause = sent[m.start():end]
            verb = m.group().lower()
            action = FORM_TO_ACTION[verb]
            needs = "S4" if verb in VERIFIED_WORDS or re.search(r"\b(delivered|confirmed|verified)\b",
                                                                 clause, re.I) else "S3"
            targets = [e.lower() for e in EMAIL_RE.findall(clause)]
            targets += [q.lower() for q in QUOTED_RE.findall(clause)]
            stripped = EMAIL_RE.sub(" ", clause)
            targets += ID_RE.findall(MONEY_RE.sub(" ", stripped))
            money = MONEY_RE.search(clause)
            cm = COUNT_RE.search(clause)
            claims.append(Claim(sentence=sent, verb=verb, action=action, needs=needs, targets=targets,
                                amount=float(money.group(1).replace(",", "")) if money else None,
                                count=_num(cm.group(1)) if cm else None))
        if any(u in low for u in UMBRELLA) or "\u2705" in sent:
            claims.append(Claim(sentence=sent, verb="(umbrella)", action="*", needs="S3", umbrella=True))
    return claims


# ----------------------------------------------------------------------------- diff
def _best(calls: list) -> str:
    return max((c["_grade"] for c in calls), key=lambda g: RANK[g], default="S0")


def check_claim(claim: Claim, calls: list) -> Finding:
    if claim.umbrella:
        if not calls:
            return Finding(claim, "gap", "S0", "umbrella claim, but the log has no tool calls")
        # read-only lookups and blocked attempts that a later call replaced don't count against "all set"
        live = [c for c in calls if not c.get("informational") and not c.get("superseded")]
        if not live:
            return Finding(claim, "gap", "S0", "umbrella claim, but the log has only read-only lookups")
        calls = live
        weak = [c for c in calls if RANK[c["_grade"]] < 3]
        if weak:
            names = ", ".join(f"{c.get('tool', '?')} ({c['_grade']})" for c in weak)
            return Finding(claim, "gap", _best(weak), f"umbrella claim, but these calls are below S3: {names}",
                           [c["_i"] for c in weak])
        return Finding(claim, "ok", _best(calls), "every call in the log is S3 or better")

    same = [c for c in calls if c.get("action") == claim.action]
    if not same:
        return Finding(claim, "gap", "S0", f"no '{claim.action}' call in the log")
    matched = same
    if claim.targets:
        matched = [c for c in same if any(t in _target_text(c) for t in claim.targets)]
        # an imported call whose arguments were not recorded may be the one the claim means
        matched += [c for c in same if c.get("target_unknown") and c not in matched]
        if not matched:
            seen = ", ".join(sorted({_target_text(c).strip() or "?" for c in same}))
            return Finding(claim, "gap", "S0",
                           f"wrong target: claim names {claim.targets}, log has '{claim.action}' for: {seen}")
    grade = _best(matched)
    ids = [c["_i"] for c in matched]
    if claim.amount is not None:
        amounts = {float(c["amount"]) for c in matched if c.get("amount") is not None}
        if amounts and claim.amount not in amounts:
            return Finding(claim, "gap", grade,
                           f"amount mismatch: claim says {claim.amount:g}, log shows "
                           + ", ".join(f"{a:g}" for a in sorted(amounts)), ids)
    if claim.count:
        # count distinct targets, so a retried send is not counted twice
        good = {json.dumps(c.get("target"), sort_keys=True) for c in matched if RANK[c["_grade"]] >= RANK[claim.needs]}
        if len(good) < claim.count:
            return Finding(claim, "gap", grade,
                           f"partial batch: {len(good)} of {claim.count} reached {claim.needs}", ids)
        return Finding(claim, "ok", grade, f"{len(good)} of {claim.count} reached {claim.needs}", ids)
    if RANK[grade] < RANK[claim.needs]:
        return Finding(claim, "gap", grade, f"wording needs {claim.needs}, best evidence is {grade}"
                       + _why(matched, grade), ids)
    return Finding(claim, "ok", grade, f"supported at {grade}", ids)


def _why(calls: list, grade: str) -> str:
    """The note of the best call, when an importer left one (e.g. 'planlock binding_violation: ...')."""
    for c in calls:
        if c["_grade"] == grade and c.get("note"):
            return f" ({c['note']})"
    return ""


def find_duplicates(calls: list) -> list:
    """Two live calls with the same tool, action and target and no shared idempotency key."""
    groups = {}
    for c in calls:
        if RANK[c["_grade"]] < 2 or c.get("repeat_ok") or c.get("informational"):
            continue
        key = (c.get("tool"), c.get("action"), json.dumps(c.get("target"), sort_keys=True))
        groups.setdefault(key, []).append(c)
    dupes = []
    for (tool, action, target), group in groups.items():
        keys = {c.get("idempotency_key") for c in group}
        if len(group) > 1 and (None in keys or len(keys) > 1):
            dupes.append({"tool": tool, "action": action, "target": json.loads(target),
                          "calls": [c["_i"] for c in group]})
    return dupes


@dataclass
class Report:
    findings: list
    duplicates: list
    calls: list = field(default_factory=list, repr=False)
    imported: object = field(default=None, repr=False)   # adapters.Imported when the log was imported

    @property
    def gaps(self) -> list:
        return [f for f in self.findings if f.status == "gap"]

    @property
    def gap_rate(self) -> float:
        return len(self.gaps) / len(self.findings) if self.findings else 0.0

    @property
    def stop_ship(self) -> bool:
        return any(f.claim.action in ("pay", "delete") for f in self.gaps)

    def to_dict(self) -> dict:
        d = {"claims": len(self.findings), "gaps": len(self.gaps), "gap_rate": round(self.gap_rate, 3),
             "stop_ship": self.stop_ship, "duplicates": self.duplicates,
             "findings": [f.to_dict() for f in self.findings]}
        if self.imported is not None:
            d["source"] = {"format": self.imported.format, "meta": self.imported.meta,
                           "warnings": self.imported.warnings}
            d["calls"] = [{k: v for k, v in c.items() if k != "_i"} for c in self.calls]
        return d

    def to_text(self) -> str:
        lines = []
        if self.imported is not None:
            m = self.imported.meta
            integ = m.get("integrity") or {}
            chain = f"; integrity: {integ['detail']}" if integ.get("ok") else ""
            lines.append(f"(imported {m.get('calls', len(self.calls))} call(s) from {self.imported.format} log{chain})")
            lines += [f"[WARN] {w}" for w in self.imported.warnings]
        for f in self.findings:
            mark = "GAP" if f.status == "gap" else "ok "
            lines.append(f"[{mark}] {f.claim.verb:<11} {f.grade:<2}  {f.reason}")
            lines.append(f"       \"{f.claim.sentence}\"")
        for d in self.duplicates:
            lines.append(f"[DUP] {d['tool']} {d['action']} {d['target']} logged {len(d['calls'])} times "
                         f"without one idempotency key (entries {d['calls']})")
        lines.append(f"\n{len(self.gaps)} gap(s) in {len(self.findings)} claim(s), gap rate {self.gap_rate:.0%}"
                     + ("  STOP-SHIP: a payment or delete claim is unsupported" if self.stop_ship else ""))
        return "\n".join(lines)


def diff(message: str, log, source: str = "native", mapping=None, **opts) -> Report:
    """Check a reply against a log. source: "native" (default), "planlock", "agt" or "auto".

    For an imported source, `log` is a file path, `mapping` an optional {tool: action} dict, and opts go to
    the importer (planlock: outbox=, profile=; agt: secret_env=).
    """
    imported = None
    if source != "native":
        from .adapters import import_log
        imported = import_log(log, source, mapping=mapping, **opts)
        log = imported.calls
    calls = load_log(log)
    findings = [check_claim(c, calls) for c in extract_claims(message)]
    if imported is not None:
        for f in findings:
            if f.reason.startswith("no '") and calls:
                f.reason += " (actions were inferred from tool names; pass a tool map to set them)"
    return Report(findings, find_duplicates(calls), calls, imported)
