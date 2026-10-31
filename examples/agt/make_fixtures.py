"""Regenerate the AGT fixtures in this folder with the real toolkit (pip install agent-governance-toolkit-core).

    python -W ignore make_fixtures.py [--out DIR]

Writes AGT audit fixtures with the real agent-governance-toolkit-core 5.0.0 AuditLog + FileAuditSink.
Each audit.log() call mirrors, argument for argument, what an AGT integration writes:
 - MAF middleware (agent_os/integrations/maf_adapter.py): policy_evaluation on input, tool_invocation
   start/complete per tool with resource=func_name, tool_blocked on a capability deny.
 - agentmesh proxy (agentmesh/cli/proxy.py): mcp_tool_call with the tool's arguments, before forwarding.
"""
import argparse, json, os, time, warnings
warnings.simplefilter("ignore")
from agentmesh.governance.audit import AuditLog
from agentmesh.governance.audit_backends import FileAuditSink

SECRET = b"saydo-diff-fixture-secret"   # fixture only; check signatures with AGT_AUDIT_SECRET=saydo-diff-fixture-secret
def maf():
    sink = FileAuditSink(path="maf_audit.jsonl", secret_key=SECRET, max_file_size=0)
    a = AuditLog(sink=sink)
    agent = "support-agent"
    a.log(event_type="agent_invocation", agent_did=agent, action="start",
          data={"agent_name": agent, "message_count": 3, "stream": False}, outcome="success")
    a.log(event_type="policy_evaluation", agent_did=agent, action="allow",
          data={"message_preview": "Customer Dana Reyes (dana@example.com) was double charged on order 7731. Refund the duplicate charge, close the duplicate account, and tell her."},
          outcome="success", policy_decision="allow")
    def tool(name, preview=None, complete=True):
        a.log(event_type="tool_invocation", agent_did="capability-guard", action="start", resource=name,
              data={"tool": name}, outcome="success")
        if complete:
            a.log(event_type="tool_invocation", agent_did="capability-guard", action="complete", resource=name,
                  data={"tool": name, "result_preview": preview[:500]}, outcome="success")
    tool("lookup_order", str({"order_id": "ord_7731", "customer": "dana@example.com", "charges": [{"id": "ch_3QF1aa", "amount": 42.0}, {"id": "ch_3QF1ab", "amount": 42.0}]}))
    tool("issue_refund", str({"refund_id": "re_3QF8x2LkT", "charge": "ch_3QF1ab", "order_id": "ord_7731", "amount": 42.0, "currency": "usd", "status": "succeeded"}))
    a.log(event_type="tool_blocked", agent_did="capability-guard", action="deny", resource="delete_customer_account",
          data={"tool": "delete_customer_account", "reason": "Tool 'delete_customer_account' is in denied_tools"}, outcome="denied")
    tool("send_email", complete=False)   # the tool raised: MAF middleware writes no 'complete' entry
    tool("post_slack_message", str({"ok": True, "channel": "support-escalations", "message_id": "msg_01HZX4QK"}))
    a.log(event_type="agent_invocation", agent_did=agent, action="complete",
          data={"agent_name": agent, "elapsed_seconds": 4.2113, "start_entry_id": "see first row"}, outcome="success")
    ok, err = sink.verify_integrity(); assert ok, err
    sink.close()
    with open("maf_export.json", "w") as f:
        json.dump(a.export(), f, indent=2, default=str)
    with open("maf_cloudevents.json", "w") as f:
        json.dump(a.export_cloudevents(), f, indent=2, default=str)

def proxy():
    sink = FileAuditSink(path="proxy_audit.jsonl", secret_key=SECRET, max_file_size=0)
    a = AuditLog(sink=sink)
    did = "did:mesh:7f3c2a91d4e8b605"
    def call(tool, arguments, allowed, policy, rule, decision):
        ts = time.strftime("%Y-%m-%dT%H:%M:%S", time.gmtime())
        entry = {"timestamp": ts + "Z", "agent": did, "action": "mcp_tool_call", "tool": tool, "arguments": arguments,
                 "decision": decision, "allowed": allowed, "policy": policy, "rule": rule, "trust_score": 800}
        a.log(event_type="mcp_tool_call", agent_did=did, action=tool, data=entry,
              outcome="allowed" if allowed else "denied")
    call("read_file", {"path": "notes/q3-board-update.md"}, True, "default", None, "allow")
    call("send_email", {"to": "board@example.com", "subject": "Q3 update", "body": "..."}, True, "default", None, "allow")
    call("delete_file", {"path": "notes/q3-board-update.md"}, False, "default", "block-destructive", "deny")
    sink.close()
if __name__ == "__main__":
    ap = argparse.ArgumentParser()
    ap.add_argument("--out", default=os.path.dirname(os.path.abspath(__file__)))
    out = ap.parse_args().out
    os.makedirs(out, exist_ok=True)
    os.chdir(out)
    for f in ("maf_audit.jsonl", "proxy_audit.jsonl"):
        if os.path.exists(f):
            os.remove(f)
    maf()
    proxy()
