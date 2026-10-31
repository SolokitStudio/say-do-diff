"""planlock and AGT importers, on the committed fixtures and on edited copies of them."""
import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stderr, redirect_stdout
from unittest import mock

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from saydo_diff import diff  # noqa: E402
from saydo_diff.__main__ import main  # noqa: E402
from saydo_diff.adapters import detect, import_log, load_mapping  # noqa: E402
from saydo_diff.adapters._common import READ_ONLY, infer_action, parse_preview  # noqa: E402
from saydo_diff.planview import build, render  # noqa: E402

PL = os.path.join(ROOT, "examples", "planlock")
AGT = os.path.join(ROOT, "examples", "agt")
PL_LOG = os.path.join(PL, "session_events.log")
PL_MAP = load_mapping(os.path.join(PL, "tool_map.json"))
SECRET = "saydo-diff-fixture-secret"


def read(path):
    with open(path, encoding="utf-8") as fh:
        return fh.read()


def rows(path):
    return [json.loads(x) for x in read(path).splitlines() if x.strip()]


def write_rows(path, rs):
    with open(path, "w", encoding="utf-8") as fh:
        fh.write("".join(json.dumps(r) + "\n" for r in rs))


def summary(report):
    return [(f.claim.verb, f.status, f.grade) for f in report.findings]


def run_cli(*argv):
    out, err = io.StringIO(), io.StringIO()
    with redirect_stdout(out), redirect_stderr(err):
        code = main(list(argv))
    return code, out.getvalue(), err.getvalue()


class InferActionTest(unittest.TestCase):
    def test_names(self):
        cases = {"send_email": "send", "issue_refund": "pay", "stripe.refunds.create": "pay",
                 "crm.customers.delete": "delete", "post_slack_message": "post", "reserveFunds": "book",
                 "submitToNetwork": "send", "lookup_order": READ_ONLY, "getAccountBalance": READ_ONLY,
                 "read_file": READ_ONLY, "delete_customer_account": "delete", "frobnicate": "call"}
        for tool, want in cases.items():
            self.assertEqual(infer_action(tool), want, tool)

    def test_mapping_wins(self):
        self.assertEqual(infer_action("submitToNetwork", {"submitToNetwork": "pay"}), "pay")
        self.assertIs(infer_action("assembleInstruction", {"assembleInstruction": "read"}), READ_ONLY)

    def test_load_mapping_shapes(self):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "m.json")
            with open(p, "w") as fh:
                json.dump({"tools": {"x": "pay"}}, fh)
            self.assertEqual(load_mapping(p), {"x": "pay"})
        self.assertIsNone(load_mapping(None))

    def test_parse_python_repr_preview(self):
        self.assertEqual(parse_preview("{'ok': True, 'id': 'x_1'}"), {"ok": True, "id": "x_1"})
        self.assertIsNone(parse_preview("not a dict"))


class DetectTest(unittest.TestCase):
    def test_detect(self):
        self.assertEqual(detect(PL_LOG), "planlock")
        for f in ("maf_audit.jsonl", "maf_export.json", "maf_cloudevents.json", "proxy_audit.jsonl", "ts_audit.json"):
            self.assertEqual(detect(os.path.join(AGT, f)), "agt", f)
        self.assertEqual(detect(os.path.join(ROOT, "examples", "ci", "runs", "refund-ok", "tool_log.json")), "native")


class PlanlockFixtureTest(unittest.TestCase):
    def setUp(self):
        self.r = diff(read(os.path.join(PL, "reply.txt")), PL_LOG, "planlock", mapping=PL_MAP)

    def test_claims(self):
        self.assertEqual(summary(self.r), [("reserved", "ok", "S3"), ("paid", "ok", "S3"),
                                           ("posted", "gap", "F"), ("(umbrella)", "gap", "F")])
        self.assertFalse(self.r.stop_ship)
        self.assertIn("pin_mismatch (expected 'USD', got 'EUR')", self.r.findings[2].reason)

    def test_calls_and_packets(self):
        imp = self.r.imported
        self.assertEqual(imp.meta["job_packets"], 3)
        self.assertEqual(imp.meta["plans"], 1)
        self.assertEqual(len(self.r.calls), 7)
        pay = [c for c in self.r.calls if c["tool"] == "submitToNetwork" and c["grade"] == "S3"][0]
        self.assertEqual(pay["target"], {"recipient": "vendor-20417"})
        self.assertEqual(pay["amount"], 25)
        self.assertEqual(pay["args_source"], "job_packet")
        self.assertEqual(pay["step"]["name"], "pay")
        self.assertEqual(imp.meta["skipped"].get("planlock control call (agreement_submit)"), 1)

    def test_blocked_attempt_is_superseded(self):
        first = [c for c in self.r.calls if c["tool"] == "reserveFunds"][0]
        self.assertEqual(first["grade"], "F")
        self.assertTrue(first.get("superseded"))
        self.assertTrue(first.get("target_unknown"))

    def test_investigation_tools_are_lookups(self):
        cc = [c for c in self.r.calls if c["tool"] == "checkCompliance"][0]
        self.assertTrue(cc["informational"])
        denied = [c for c in self.r.calls if c["source"]["route"] == "GET /bug/diagnose" and c["grade"] == "F"]
        self.assertEqual(len(denied), 1)

    def test_plan_view(self):
        plans = build(self.r)
        self.assertEqual(len(plans), 1)
        p = plans[0]
        self.assertEqual(p["status"], "on_hold")
        self.assertEqual([s["flag"] for s in p["steps"]],
                         ["planned, done, claimed", "planned, done, claimed", "CLAIMED BUT NOT DONE"])
        self.assertEqual(len(p["outside_plan"]), 2)
        text = render(self.r)
        self.assertIn("Step 3 ledger: ON HOLD (audit FAILED)  -> CLAIMED BUT NOT DONE", text)
        self.assertIn("pin getAccountBalance.currency == 'USD'", text)
        self.assertNotIn("\n\n\n", text)

    def test_without_map_pay_is_a_send(self):
        r = diff(read(os.path.join(PL, "reply.txt")), PL_LOG, "planlock")
        paid = [f for f in r.findings if f.claim.verb == "paid"][0]
        self.assertEqual(paid.status, "gap")
        self.assertIn("pass a tool map", paid.reason)
        self.assertTrue(r.stop_ship)

    def test_cli(self):
        code, out, _ = run_cli("-m", os.path.join(PL, "reply.txt"), "-l", PL_LOG, "--from", "planlock",
                               "--map", os.path.join(PL, "tool_map.json"), "--plan")
        self.assertEqual(code, 1)
        self.assertIn("PLAN 1: Pay recipient vendor-20417", out)
        code, out, _ = run_cli("-m", os.path.join(PL, "reply.txt"), "-l", PL_LOG, "--from", "auto",
                               "--map", os.path.join(PL, "tool_map.json"), "--plan", "--json")
        d = json.loads(out)
        self.assertEqual(d["source"]["format"], "planlock")
        self.assertEqual(d["plans"][0]["steps"][2]["flag"], "CLAIMED BUT NOT DONE")
        self.assertEqual(len(d["calls"]), 7)

    def test_emit_log_roundtrip(self):
        with tempfile.TemporaryDirectory() as d:
            out = os.path.join(d, "log.json")
            run_cli("-m", os.path.join(PL, "reply.txt"), "-l", PL_LOG, "--from", "planlock",
                    "--map", os.path.join(PL, "tool_map.json"), "--emit-log", out)
            again = diff(read(os.path.join(PL, "reply.txt")), out)
            self.assertEqual(summary(again), summary(self.r))

    def test_require_integrity_has_nothing_to_verify(self):
        code, _, err = run_cli("-m", os.path.join(PL, "reply.txt"), "-l", PL_LOG, "--from", "planlock",
                               "--require-integrity")
        self.assertEqual(code, 3)
        self.assertIn("integrity not verified", err)


class PlanlockEditedLogTest(unittest.TestCase):
    """Edited copies of the fixture, for paths the recorded run did not hit."""

    def setUp(self):
        self.d = tempfile.mkdtemp()
        self.addCleanup(shutil.rmtree, self.d)
        shutil.copytree(os.path.join(PL, "vm_io"), os.path.join(self.d, "vm_io"))
        self.rows = rows(PL_LOG)
        for r in self.rows:  # the recorded absolute outbox_dir; fall back to <logdir>/vm_io/outbox
            if "outbox_dir" in r:
                r["outbox_dir"] = "/nonexistent/vm_io"
        self.txn = [r["txn_id"] for r in self.rows if r.get("record_type") == "txn_started"
                    and r["route"] == "PATCH /feature/implement/active"]

    def run_rows(self, rs, reply=None, **opts):
        p = os.path.join(self.d, "session_events.log")
        write_rows(p, rs)
        reply = reply or read(os.path.join(PL, "reply.txt"))
        return diff(reply, p, "planlock", mapping=PL_MAP, **opts)

    def test_relative_outbox_fallback(self):
        r = self.run_rows(self.rows)
        self.assertEqual(r.imported.meta["job_packets"], 3)

    def test_no_packets_uses_plan_constraints(self):
        shutil.rmtree(os.path.join(self.d, "vm_io"))
        r = self.run_rows(self.rows)
        self.assertEqual(r.imported.meta["job_packets"], 0)
        self.assertTrue(any("no planlock job packets" in w for w in r.imported.warnings))
        pay = [c for c in r.calls if c["tool"] == "submitToNetwork" and c["grade"] == "S3"][0]
        self.assertEqual(pay["target"], {"recipient": "vendor-20417"})
        self.assertEqual(pay["args_source"], "plan")
        self.assertEqual(summary(r)[:2], [("reserved", "ok", "S3"), ("paid", "ok", "S3")])

    def test_explicit_outbox(self):
        shutil.move(os.path.join(self.d, "vm_io"), os.path.join(self.d, "elsewhere"))
        r = self.run_rows(self.rows, outbox=os.path.join(self.d, "elsewhere", "outbox"))
        self.assertEqual(r.imported.meta["job_packets"], 3)

    def test_upstream_failure_fails_the_payment(self):
        rs = list(self.rows)
        i = next(n for n, r in enumerate(rs) if r.get("record_type") == "tool_call" and r.get("tool") == "submitToNetwork")
        rs.insert(i + 1, {"timestamp": rs[i]["timestamp"], "record_type": "upstream_failure", "txn_id": self.txn[1],
                          "route": "PATCH /feature/implement/active", "tool": "submitToNetwork"})
        r = self.run_rows(rs)
        paid = [f for f in r.findings if f.claim.verb == "paid"][0]
        self.assertEqual((paid.status, paid.grade), ("gap", "F"))
        self.assertTrue(r.stop_ship)

    def test_tool_call_error(self):
        rs = [dict(r) for r in self.rows]
        for r in rs:
            if r.get("record_type") == "tool_call" and r.get("tool") == "reserveFunds":
                r["record_type"], r["error"] = "tool_call_error", "upstream timeout after 30s"
        r = self.run_rows(rs)
        res = [f for f in r.findings if f.claim.verb == "reserved"][0]
        self.assertEqual((res.status, res.grade), ("gap", "S1"))
        self.assertIn("upstream timeout", res.reason)

    def test_override_and_completion(self):
        rs = list(self.rows) + [
            {"timestamp": "2026-10-31T01:30:00.000Z", "event_name": "FAILURE_OVERRIDDEN_AND_PROMOTED",
             "event_payload": {"resolved_step_index": 3, "next_step_index": 4,
                               "justification": "ledger posted by finance manually"}}]
        plan = build(self.run_rows(rs))[0]
        self.assertEqual(plan["status"], "completed")
        self.assertEqual(plan["steps"][2]["status"], "overridden")
        self.assertIn("ledger posted by finance manually", render(self.run_rows(rs)))

    def test_abort(self):
        rs = list(self.rows) + [{"timestamp": "2026-10-31T01:30:00.000Z", "event_name": "SEQUENTIAL_PLAN_ABORTED",
                                 "event_payload": {"reason": "operator aborted"}}]
        plan = build(self.run_rows(rs))[0]
        self.assertEqual(plan["status"], "aborted")

    def test_bad_line_warns(self):
        p = os.path.join(self.d, "session_events.log")
        write_rows(p, self.rows)
        with open(p, "a") as fh:
            fh.write("{truncated\n")
        imp = import_log(p, "planlock", PL_MAP)
        self.assertTrue(any("not JSON" in w for w in imp.warnings))

    def test_profile_marks_lookups(self):
        r = self.run_rows(self.rows, profile=os.path.join(PL, "profile.micropayout.json"))
        self.assertTrue(all(c.get("informational") for c in r.calls if c["tool"] == "checkCompliance"))

    def test_mentions_unplanned_done_step(self):
        r = self.run_rows(self.rows, reply="I reserved €25 on acc_main.")
        flags = [s["flag"] for s in build(r)[0]["steps"]]
        self.assertEqual(flags[1], "done, not mentioned in the reply")


class AgtFixtureTest(unittest.TestCase):
    EXPECTED = [("refunded", "ok", "S3"), ("emailed", "gap", "S1"), ("deleted", "gap", "F"),
                ("posted", "ok", "S3"), ("(umbrella)", "gap", "S1")]

    def setUp(self):
        self.reply = read(os.path.join(AGT, "reply.txt"))

    def test_three_containers_same_result(self):
        for f in ("maf_audit.jsonl", "maf_export.json", "maf_cloudevents.json"):
            r = diff(self.reply, os.path.join(AGT, f), "agt")
            self.assertEqual(summary(r), self.EXPECTED, f)
            self.assertTrue(r.stop_ship)
            self.assertIs(r.imported.meta["integrity"]["ok"], True, f)

    def test_refund_carries_result_id(self):
        r = diff(self.reply, os.path.join(AGT, "maf_audit.jsonl"), "agt")
        ref = [c for c in r.calls if c["tool"] == "issue_refund"][0]
        self.assertEqual(ref["id"], "re_3QF8x2LkT")
        self.assertEqual(ref["amount"], 42.0)
        self.assertEqual(ref["source"]["kind"], "signed")
        self.assertIn("complete_line", ref["source"])
        lookups = [c for c in r.calls if c["tool"] == "lookup_order"]
        self.assertTrue(lookups[0]["informational"])

    def test_hmac(self):
        p = os.path.join(AGT, "maf_audit.jsonl")
        with mock.patch.dict(os.environ, {"AGT_AUDIT_SECRET": SECRET}):
            self.assertIn("HMAC", import_log(p, "agt").meta["integrity"]["detail"])
        with mock.patch.dict(os.environ, {"AGT_AUDIT_SECRET": "wrong"}):
            integ = import_log(p, "agt").meta["integrity"]
            self.assertIs(integ["ok"], False)
        with mock.patch.dict(os.environ, {"MY_SECRET": SECRET}):
            self.assertIs(import_log(p, "agt", secret_env="MY_SECRET").meta["integrity"]["ok"], True)

    def test_tamper_detected_everywhere(self):
        with tempfile.TemporaryDirectory() as d:
            rs = rows(os.path.join(AGT, "maf_audit.jsonl"))
            rs[5]["data"]["result_preview"] = rs[5]["data"]["result_preview"].replace("42.0", "4200.0")
            p = os.path.join(d, "a.jsonl")
            write_rows(p, rs)
            imp = import_log(p, "agt")
            self.assertIs(imp.meta["integrity"]["ok"], False)
            self.assertEqual(imp.meta["integrity"]["line"], 6)
            self.assertTrue(any("BROKEN" in w for w in imp.warnings))

            ex = json.loads(read(os.path.join(AGT, "maf_export.json")))
            ex["entries"].pop(3)
            p = os.path.join(d, "e.json")
            with open(p, "w") as fh:
                json.dump(ex, fh)
            self.assertIs(import_log(p, "agt").meta["integrity"]["ok"], False)

            ts = json.loads(read(os.path.join(AGT, "ts_audit.json")))
            ts[2]["decision"] = "allow"
            p = os.path.join(d, "t.json")
            with open(p, "w") as fh:
                json.dump(ts, fh)
            self.assertIs(import_log(p, "agt").meta["integrity"]["ok"], False)
            code, _, err = run_cli("-m", os.path.join(AGT, "reply.txt"), "-l", p, "--from", "agt",
                                   "--require-integrity")
            self.assertEqual(code, 3)

    def test_proxy_is_decision_only(self):
        r = diff(read(os.path.join(AGT, "proxy_reply.txt")), os.path.join(AGT, "proxy_audit.jsonl"), "auto")
        self.assertEqual(summary(r), [("sent", "gap", "S1"), ("deleted", "gap", "F")])
        self.assertTrue(any("policy decisions only" in w for w in r.imported.warnings))
        send = [c for c in r.calls if c["tool"] == "send_email"][0]
        self.assertEqual(send["target"].get("to"), "board@example.com")

    def test_typescript_sdk(self):
        r = diff("I refunded the customer and deleted the CRM record.", os.path.join(AGT, "ts_audit.json"), "agt")
        self.assertEqual(summary(r), [("refunded", "gap", "S1"), ("deleted", "gap", "F")])
        self.assertEqual(r.imported.meta["shapes"], ["sdk"])
        self.assertIn("TypeScript", r.imported.meta["integrity"]["detail"])

    def test_cli_exit_and_text(self):
        code, out, _ = run_cli("-m", os.path.join(AGT, "reply.txt"), "-l", os.path.join(AGT, "maf_audit.jsonl"),
                               "--from", "agt")
        self.assertEqual(code, 2)
        self.assertIn("imported 5 call(s) from agt log; integrity:", out)


class AgtShapesTest(unittest.TestCase):
    def go(self, entries, msg, jsonl=False):
        with tempfile.TemporaryDirectory() as d:
            p = os.path.join(d, "log")
            with open(p, "w") as fh:
                if jsonl:
                    fh.write("".join(json.dumps(e) + "\n" for e in entries))
                else:
                    json.dump(entries, fh)
            return diff(msg, p, "agt")

    def test_mcp_gateway(self):
        r = self.go([
            {"timestamp": 1793410000.1, "agent_id": "support-bot", "tool_name": "send_email",
             "parameters": {"to": "dana@example.com"}, "allowed": True, "reason": "allowed",
             "approval_status": None},
            {"timestamp": 1793410001.2, "agent_id": "support-bot", "tool_name": "issue_refund",
             "parameters": {"order_id": "7731", "amount": 42}, "allowed": False, "reason": "requires approval",
             "approval_status": "pending"},
        ], "I emailed dana@example.com and refunded $42 for order 7731.")
        self.assertEqual(summary(r), [("emailed", "gap", "S1"), ("refunded", "gap", "S1")])
        self.assertIn("held for review", r.findings[1].reason)

    def test_base_agent(self):
        r = self.go([
            {"timestamp": "2026-10-30T18:00:00", "agent_id": "a", "request_id": "r1", "action": "send_email",
             "params_keys": ["to", "body"], "decision": "allow", "result_success": True, "error": None,
             "execution_time_ms": 12.5},
            {"timestamp": "2026-10-30T18:00:01", "agent_id": "a", "request_id": "r2", "action": "delete_record",
             "params_keys": ["id"], "decision": "allow", "result_success": False, "error": "404",
             "execution_time_ms": 3.0},
        ], "I sent the email and deleted the record.", jsonl=True)
        self.assertEqual([c["grade"] for c in r.calls], ["S2", "F"])
        self.assertTrue(r.stop_ship)

    def test_governance_logger(self):
        r = self.go([
            {"timestamp": "2026-10-30T18:00:00Z", "event_type": "governance_decision", "agent_id": "a",
             "action": "transfer_funds", "decision": "deny", "reason": "over limit", "latency_ms": 0.4,
             "metadata": {}},
        ], "I transferred the funds.", jsonl=True)
        self.assertEqual(r.calls[0]["status"], "blocked")
        self.assertIn("over limit", r.calls[0]["note"])

    def test_go_links(self):
        ents = [{"timestamp": "2026-10-30T18:00:00Z", "agent_id": "a", "action": "send_email", "decision": "allow",
                 "hash": "h1", "previous_hash": ""},
                {"timestamp": "2026-10-30T18:00:01Z", "agent_id": "a", "action": "post_message", "decision": "allow",
                 "hash": "h2", "previous_hash": "h1"}]
        self.assertIs(self.go(ents, "I posted it.").imported.meta["integrity"]["ok"], True)
        ents[1]["previous_hash"] = "zz"
        self.assertIs(self.go(ents, "I posted it.").imported.meta["integrity"]["ok"], False)

    def test_failed_outcome_and_unpaired_complete(self):
        base = {"agent_did": "did:mesh:x", "trace_id": "t", "session_id": "s", "target_did": None,
                "policy_decision": None, "matched_rule": None}
        r = self.go([
            {**base, "entry_id": "1", "timestamp": "2026-10-30T18:00:00", "event_type": "agent_action",
             "action": "charge", "resource": "charge_card", "data": {"amount": 10}, "outcome": "failure"},
            {**base, "entry_id": "2", "timestamp": "2026-10-30T18:00:01", "event_type": "tool_invocation",
             "action": "complete", "resource": "post_message", "data": {"result_preview": "{'id': 'msg_9'}"},
             "outcome": "success"},
        ], "I charged the card and posted the message.")
        self.assertEqual([(c["tool"], c["grade"]) for c in r.calls], [("charge_card", "F"), ("post_message", "S3")])

    def test_unknown_rows_warn(self):
        r = self.go([{"hello": "world"}, {"timestamp": "x", "agentId": "a", "action": "send_email",
                                          "decision": "allow"}], "I sent it.")
        self.assertTrue(any("not a known AGT audit shape" in w for w in r.imported.warnings))


if __name__ == "__main__":
    unittest.main()
