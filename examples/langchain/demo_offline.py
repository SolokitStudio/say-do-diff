"""Run the callback against simulated tool events. No LangChain, model or API key needed.

    python3 examples/langchain/demo_offline.py

It replays the events LangChain would send for three tool calls (an email that was only
queued, a calendar event with an id, a refund that timed out) and checks a reply against them.
"""
import os
import sys
import uuid

sys.path.insert(0, os.path.dirname(os.path.abspath(__file__)))
from saydo_callback import SayDoCallbackHandler  # noqa: E402

TOOL_MAP = {
    "send_email": {"tool": "gmail.send", "action": "send", "target": "to", "id": "id", "status": "status"},
    "create_event": {"tool": "gcal.create", "action": "book", "target": "title", "id": "id"},
    "issue_refund": {"tool": "stripe.refund", "action": "pay", "target": "order", "amount": "amount",
                     "id": "id", "status": "status"},
    "search_docs": None,  # read-only; not listed in a real map
}


def replay(handler):
    events = [
        ("send_email", {"to": "dana@acme.com", "subject": "Proposal"}, {"status": "queued"}, None),
        ("create_event", {"title": "Dana demo Tuesday"}, '{"id": "evt_5521", "status": "done"}', None),
        ("issue_refund", {"order": "order 88412", "amount": 45}, None, TimeoutError("stripe timed out")),
    ]
    for name, args, out, err in events:
        rid = uuid.uuid4()
        handler.on_tool_start({"name": name}, str(args), run_id=rid, inputs=args)
        if err:
            handler.on_tool_error(err, run_id=rid)
        else:
            handler.on_tool_end(out, run_id=rid)


if __name__ == "__main__":
    h = SayDoCallbackHandler({k: v for k, v in TOOL_MAP.items() if v})
    replay(h)
    reply = ("I sent the proposal to dana@acme.com and booked the demo with Dana for Tuesday. "
             "I also refunded $45 for order 88412.")
    report = h.check(reply)
    print(report.to_text())
    sys.exit(2 if report.stop_ship else 1 if report.gaps else 0)
