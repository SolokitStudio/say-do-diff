import io
import json
import os
import subprocess
import sys
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)

from saydo_diff import diff, extract_claims, grade_call  # noqa: E402
from saydo_diff.__main__ import main  # noqa: E402

EX = os.path.join(ROOT, "examples")


def one(message, log):
    r = diff(message, log)
    return r.findings[0] if r.findings else None


class Grades(unittest.TestCase):
    def test_grade_table(self):
        cases = [
            ({}, "S1"),
            ({"error": "timeout"}, "S1"),
            ({"status": "timeout"}, "S1"),
            ({"http_status": 202}, "S2"),
            ({"status": "queued"}, "S2"),
            ({"status": "done"}, "S2"),                   # done without an object id is not S3
            ({"status": "done", "id": "m1"}, "S3"),
            ({"http_status": 200, "id": "m1"}, "S3"),
            ({"status": "delivered", "id": "m1"}, "S4"),
            ({"status": "bounced"}, "F"),
            ({"http_status": 500}, "F"),
            ({"grade": "s4"}, "S4"),
        ]
        for call, want in cases:
            with self.subTest(call=call):
                self.assertEqual(grade_call(call), want)


class Claims(unittest.TestCase):
    def test_future_and_negated_are_not_claims(self):
        text = "I will send the report tomorrow. I haven't booked the room yet. Should I pay the invoice?"
        self.assertEqual([c.verb for c in extract_claims(text)], [])

    def test_two_claims_in_one_sentence(self):
        cs = extract_claims("I sent the proposal to dana@acme.com and booked the call.")
        self.assertEqual([(c.action, c.targets) for c in cs], [("send", ["dana@acme.com"]), ("book", [])])

    def test_amount_count_and_id(self):
        c = extract_claims("I refunded $1,045.50 for order 88412.")[0]
        self.assertEqual((c.action, c.amount, c.targets), ("pay", 1045.5, ["88412"]))
        self.assertEqual(extract_claims("We sent all three invoices.")[0].count, 3)

    def test_delivered_needs_s4(self):
        self.assertEqual(extract_claims("The email was delivered.")[0].needs, "S4")

    def test_other_subject_is_not_our_claim(self):
        self.assertEqual(extract_claims("Thanks for the file you sent."), [])


class Diff(unittest.TestCase):
    def test_supported_claim(self):
        f = one("I sent the proposal to dana@acme.com.",
                [{"tool": "mail", "action": "send", "target": "dana@acme.com", "status": "done", "id": "m1"}])
        self.assertEqual((f.status, f.grade), ("ok", "S3"))

    def test_no_call(self):
        f = one("I booked the room.", [])
        self.assertEqual((f.status, f.grade), ("gap", "S0"))

    def test_queued_is_not_sent(self):
        f = one("I sent it.", [{"action": "send", "http_status": 202}])
        self.assertEqual((f.status, f.grade), ("gap", "S2"))

    def test_failed(self):
        f = one("I sent it.", [{"action": "send", "status": "bounced"}])
        self.assertEqual((f.status, f.grade), ("gap", "F"))

    def test_wrong_target(self):
        f = one("I emailed dana@acme.com.",
                [{"action": "send", "target": "dan@acme.com", "status": "done", "id": "m1"}])
        self.assertEqual(f.status, "gap")
        self.assertIn("wrong target", f.reason)

    def test_amount_mismatch(self):
        f = one("I refunded $45 for order 88412.",
                [{"action": "pay", "target": "order 88412", "amount": 40, "status": "done", "id": "re_1"}])
        self.assertIn("amount mismatch", f.reason)

    def test_partial_batch_ignores_retries(self):
        log = [{"action": "send", "target": f"inv {i}", "status": "done", "id": f"m{i}"} for i in (1, 2, 3)]
        log.append({"action": "send", "target": "inv 3", "status": "done", "id": "m4"})
        f = one("I sent all 5 invoices.", log)
        self.assertEqual(f.status, "gap")
        self.assertIn("3 of 5", f.reason)

    def test_umbrella(self):
        r = diff("All set!", [{"tool": "cal", "action": "book", "status": "pending"}])
        self.assertEqual(r.findings[0].status, "gap")
        self.assertEqual(diff("All set!", []).findings[0].reason,
                         "umbrella claim, but the log has no tool calls")
        ok = diff("All set!", [{"action": "book", "status": "done", "id": "e1"}])
        self.assertEqual(ok.findings[0].status, "ok")

    def test_duplicates(self):
        log = [{"tool": "mail", "action": "send", "target": "a@b.co", "status": "done", "id": "m1"},
               {"tool": "mail", "action": "send", "target": "a@b.co", "status": "done", "id": "m2"}]
        self.assertEqual(len(diff("", log).duplicates), 1)
        for c in log:
            c["idempotency_key"] = "k1"
        self.assertEqual(diff("", log).duplicates, [])

    def test_stop_ship_on_money(self):
        self.assertTrue(diff("I paid the invoice.", []).stop_ship)
        self.assertFalse(diff("I booked the room.", []).stop_ship)

    def test_jsonl_and_wrapped_log(self):
        line = json.dumps({"action": "send", "status": "done", "id": "m1"})
        self.assertEqual(one("I sent it.", line + "\n" + line).status, "ok")
        self.assertEqual(one("I sent it.", {"calls": [json.loads(line)]}).status, "ok")


class Example(unittest.TestCase):
    def test_example_report(self):
        msg = open(os.path.join(EX, "reply.txt")).read()
        r = diff(msg, os.path.join(EX, "tool_log.json"))
        self.assertEqual([f.status for f in r.findings], ["gap", "ok", "gap", "gap", "gap"])
        self.assertTrue(r.stop_ship)
        self.assertEqual(len(r.duplicates), 1)

    def test_cli_exit_codes(self):
        buf = io.StringIO()
        with redirect_stdout(buf):
            code = main(["-m", os.path.join(EX, "reply.txt"), "-l", os.path.join(EX, "tool_log.json"), "--json"])
        self.assertEqual(code, 2)
        self.assertEqual(json.loads(buf.getvalue())["claims"], 5)

    def test_cli_module_runs(self):
        out = subprocess.run([sys.executable, "-m", "saydo_diff", "-m", os.path.join(EX, "reply.txt"),
                              "-l", os.path.join(EX, "tool_log.json")], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(out.returncode, 2)
        self.assertIn("3 of 5", out.stdout)


if __name__ == "__main__":
    unittest.main()
