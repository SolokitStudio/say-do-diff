import json
import os
import sys
import tempfile
import unittest
import uuid

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
LC = os.path.join(ROOT, "examples", "langchain")
sys.path.insert(0, ROOT)
sys.path.insert(0, LC)

from saydo_callback import SayDoCallbackHandler  # noqa: E402
from saydo_diff.core import grade_call  # noqa: E402

try:
    import langchain_core  # noqa: F401
    HAS_LC = True
except ImportError:
    HAS_LC = False

MAP = {
    "send_email": {"tool": "gmail.send", "action": "send", "target": "to", "id": "id", "status": "status"},
    "issue_refund": {"tool": "stripe.refund", "action": "pay", "target": "order", "amount": "amount",
                     "id": "id", "status": "status"},
}


class FakeToolMessage:
    def __init__(self, content, status="success", artifact=None):
        self.content, self.status, self.artifact = content, status, artifact


def run(h, name, args, output=None, error=None):
    rid = uuid.uuid4()
    h.on_tool_start({"name": name}, json.dumps(args), run_id=rid, inputs=args)
    if error is not None:
        h.on_tool_error(error, run_id=rid)
    else:
        h.on_tool_end(output, run_id=rid)


class LangChainCallbackTest(unittest.TestCase):
    def test_dict_output_with_id_grades_s3(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "send_email", {"to": "dana@acme.com"}, {"id": "msg_1", "status": "sent"})
        self.assertEqual(h.calls, [{"tool": "gmail.send", "action": "send", "target": "dana@acme.com",
                                    "id": "msg_1", "status": "sent"}])
        self.assertEqual(grade_call(h.calls[0]), "S3")

    def test_json_string_output_is_parsed(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "send_email", {"to": "a@b.co"}, '{"status": "queued"}')
        self.assertEqual(grade_call(h.calls[0]), "S2")

    def test_plain_text_output_stays_below_s3(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "send_email", {"to": "a@b.co"}, "Email sent!")
        self.assertEqual(h.calls[0]["status"], "done")
        self.assertEqual(grade_call(h.calls[0]), "S2")

    def test_tool_error_grades_s1(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "issue_refund", {"order": "order 88412", "amount": 45}, error=TimeoutError("timed out"))
        self.assertIn("TimeoutError", h.calls[0]["error"])
        self.assertEqual(grade_call(h.calls[0]), "S1")

    def test_amount_falls_back_to_input(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "issue_refund", {"order": "order 1", "amount": 45}, {"id": "re_1", "status": "succeeded"})
        self.assertEqual(h.calls[0]["amount"], 45)

    def test_unmapped_tools_are_ignored(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "search_docs", {"q": "pricing"}, "results")
        self.assertEqual(h.calls, [])

    def test_tool_message_content_and_error_status(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "send_email", {"to": "a@b.co"}, FakeToolMessage('{"id": "m9", "status": "sent"}'))
        run(h, "send_email", {"to": "c@d.co"}, FakeToolMessage("SMTP 550", status="error"))
        self.assertEqual(grade_call(h.calls[0]), "S3")
        self.assertEqual(grade_call(h.calls[1]), "S1")

    def test_check_flags_unsupported_refund(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "send_email", {"to": "dana@acme.com"}, {"id": "msg_1", "status": "sent"})
        run(h, "issue_refund", {"order": "order 88412", "amount": 45}, error=TimeoutError("x"))
        r = h.check("I sent the proposal to dana@acme.com. I refunded $45 for order 88412.")
        self.assertEqual([f.status for f in r.findings], ["ok", "gap"])
        self.assertTrue(r.stop_ship)

    def test_write_round_trips_through_cli(self):
        from saydo_diff.__main__ import main
        h = SayDoCallbackHandler(MAP)
        run(h, "send_email", {"to": "dana@acme.com"}, {"id": "msg_1", "status": "sent"})
        with tempfile.TemporaryDirectory() as d:
            log = h.write(os.path.join(d, "runs", "tool_log.json"))
            msg = os.path.join(d, "reply.txt")
            with open(msg, "w") as f:
                f.write("I sent the proposal to dana@acme.com.")
            import io
            from contextlib import redirect_stdout
            with redirect_stdout(io.StringIO()):
                self.assertEqual(main(["--message", msg, "--log", log]), 0)

    def test_offline_demo_exits_stop_ship(self):
        import subprocess
        p = subprocess.run([sys.executable, os.path.join(LC, "demo_offline.py")], capture_output=True, text=True)
        self.assertEqual(p.returncode, 2, p.stdout + p.stderr)
        self.assertIn("[ok ] booked", p.stdout)

    def test_failed_call_keeps_attempted_amount(self):
        h = SayDoCallbackHandler(MAP)
        run(h, "issue_refund", {"order": "order 88412", "amount": 45}, error=TimeoutError("stripe timed out"))
        self.assertEqual(h.calls[0]["amount"], 45)
        self.assertEqual(h.calls[0]["target"], "order 88412")
        self.assertIn("TimeoutError", h.calls[0]["error"])
        self.assertEqual(grade_call(h.calls[0]), "S1")

    def test_python_repr_input_str_without_inputs_kwarg(self):
        # Older callers only pass input_str, which LangChain builds with str(dict), not JSON.
        h = SayDoCallbackHandler(MAP)
        args = {"order": "order 7", "amount": 12.5}
        rid = uuid.uuid4()
        h.on_tool_start({"name": "issue_refund"}, str(args), run_id=rid)
        h.on_tool_end({"id": "re_1", "status": "succeeded"}, run_id=rid)
        self.assertEqual(h.calls[0]["target"], "order 7")
        self.assertEqual(h.calls[0]["amount"], 12.5)

    @unittest.skipUnless(HAS_LC, "langchain-core not installed")
    def test_real_langchain_tool_invocation(self):
        from langchain_core.tools import tool

        @tool
        def send_email(to: str) -> dict:
            """Send an email."""
            return {"id": "msg_42", "status": "sent"}

        @tool
        def issue_refund(order: str, amount: float) -> dict:
            """Refund an order."""
            raise TimeoutError("stripe timed out")

        h = SayDoCallbackHandler(MAP)
        send_email.invoke({"to": "dana@acme.com"}, config={"callbacks": [h]})
        with self.assertRaises(TimeoutError):
            issue_refund.invoke({"order": "order 88412", "amount": 45}, config={"callbacks": [h]})
        self.assertEqual(grade_call(h.calls[0]), "S3")
        self.assertEqual(h.calls[0]["target"], "dana@acme.com")
        self.assertEqual(grade_call(h.calls[1]), "S1")
        self.assertEqual(h.calls[1]["amount"], 45)


if __name__ == "__main__":
    unittest.main()
