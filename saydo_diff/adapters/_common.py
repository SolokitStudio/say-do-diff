"""Helpers shared by the receipt importers: tool name -> action, argument -> target/amount/id."""
from __future__ import annotations

import ast
import json
import re

# First token of the tool name that appears here decides the action. Read-only verbs map to None.
_TABLE = [
    ("delete", "delete remove purge erase destroy drop wipe"),
    ("cancel", "cancel revoke void unsubscribe"),
    ("pay", "pay payout payouts refund refunds charge charges transfer transfers wire reimburse credit invoice"),
    ("book", "book reserve schedule"),
    ("send", "send email mail message notify reply forward text sms invite share submit"),
    ("post", "post publish tweet deploy release"),
    ("create", "create add open insert new file register upload save generate make"),
    ("update", "update edit modify set patch close resolve archive merge approve assign move rename enable "
               "disable mark"),
    (None, "get list read fetch search query check lookup find view describe score diagnose inspect "
           "retrieve lookup_order count download verify validate preview estimate quote balance"),
]
VERB_TO_ACTION = {w: a for a, words in _TABLE for w in words.split()}
READ_ONLY = object()

AMOUNT_KEYS = ("amount", "total", "value", "sum", "amount_usd", "amount_eur")
ID_KEYS = ("id", "object_id", "message_id", "messageId", "refund_id", "charge_id", "payment_id", "event_id",
           "reservation_id", "network_ref", "ledger_id", "ticket_id", "order_id", "invoice_id", "transaction_id",
           "ts")
STRIPE_ID_RE = re.compile(r"\b(?:re|ch|pi|in|cus|sub|evt|txn|msg|po|tr)_[A-Za-z0-9]{6,}\b")


def tokens(name: str) -> list:
    """Split 'submitToNetwork', 'issue_refund', 'stripe.refunds.create', 'post-slack' into lowercase words."""
    name = re.sub(r"([a-z0-9])([A-Z])", r"\1 \2", str(name or ""))
    return [t for t in re.split(r"[^A-Za-z0-9]+", name.lower()) if t]


def infer_action(tool: str, mapping: dict | None = None):
    """Return a say-do-diff action, READ_ONLY for a lookup tool, or "call" when nothing matches.

    An explicit mapping wins: {"submitToNetwork": "pay", "getAccountBalance": "read"}. In a mapping
    "read", "read_only" or "" marks a lookup tool. For dotted names (stripe.refunds.create) the verb
    tokens are tried before the noun tokens, so create/refund resolve on the strongest word.
    """
    if mapping and tool in mapping:
        v = mapping[tool]
        return READ_ONLY if v in ("", None, "read", "read_only", "readonly", "informational") else v
    toks = tokens(tool)
    for t in toks:
        if t in VERB_TO_ACTION:
            hit = VERB_TO_ACTION[t]
            # "stripe.refunds.create": a generic create/update verb after a money/delete noun -> the noun
            if hit in ("create", "update") or hit is None:
                stronger = [VERB_TO_ACTION[x] for x in toks if VERB_TO_ACTION.get(x) in
                            ("pay", "delete", "cancel", "book", "send", "post")]
                if stronger and hit is not None:
                    return stronger[0]
            return READ_ONLY if hit is None else hit
    return "call"


def split_args(args: dict | None):
    """args -> (target dict, amount or None)."""
    target, amount = {}, None
    for k, v in (args or {}).items():
        if k in AMOUNT_KEYS and amount is None and _is_num(v):
            amount = float(v)
        elif k not in ("op",):
            target[k] = v
    return target, amount


def _is_num(v) -> bool:
    if isinstance(v, bool):
        return False
    if isinstance(v, (int, float)):
        return True
    try:
        float(str(v))
        return True
    except ValueError:
        return False


def parse_preview(text):
    """AGT MAF middleware stores str(result)[:500]: a Python repr, JSON, or plain text. Best effort."""
    if isinstance(text, (dict, list)):
        return text
    if not isinstance(text, str) or not text.strip():
        return None
    s = text.strip()
    for loader in (json.loads, ast.literal_eval):
        try:
            return loader(s)
        except (ValueError, SyntaxError, TypeError, MemoryError, RecursionError):
            continue
    return None


def find_id(obj, text: str = "") -> str | None:
    """An object id from a result: top-level id-like keys first, then a provider-style token in the text."""
    if isinstance(obj, dict):
        for k in ID_KEYS:
            v = obj.get(k)
            if isinstance(v, (str, int)) and str(v).strip() and k not in ("order_id",):
                return str(v)
        for k, v in obj.items():
            if isinstance(v, (str, int)) and str(k).lower().endswith(("_id", "id")) and k != "order_id" and str(v):
                return str(v)
    m = STRIPE_ID_RE.search(text or "")
    return m.group(0) if m else None


def find_amount(obj):
    if isinstance(obj, dict):
        for k in AMOUNT_KEYS:
            if k in obj and _is_num(obj[k]):
                return float(obj[k])
    return None


def scalar_fields(obj, limit: int = 8) -> dict:
    """Top-level scalar fields of a result, used as extra match text for the target."""
    out = {}
    if isinstance(obj, dict):
        for k, v in obj.items():
            if isinstance(v, (str, int, float)) and not isinstance(v, bool) and len(out) < limit:
                out[k] = v
    return out


def supersede(calls: list) -> None:
    """Mark blocked / decision-only calls that a later live call of the same tool made moot."""
    from ..core import RANK
    for i, c in enumerate(calls):
        if c.get("_supersedable") and RANK[c["grade"]] < 2:
            for later in calls[i + 1:]:
                if later.get("tool") == c.get("tool") and RANK[later["grade"]] >= 2:
                    c["superseded"] = True
                    c["note"] = (c.get("note", "") + f"; superseded by a later {later['tool']} call").lstrip("; ")
                    break
    for c in calls:
        c.pop("_supersedable", None)
