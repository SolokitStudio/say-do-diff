import io
import json
import os
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
N8N = os.path.join(ROOT, "examples", "n8n")
sys.path.insert(0, ROOT)
sys.path.insert(0, N8N)

from saydo_diff import diff  # noqa: E402
from n8n_to_log import main, reply_text, to_log  # noqa: E402


def load(name):
    with open(os.path.join(N8N, name), encoding="utf-8") as f:
        return json.load(f)


class N8nAdapterTest(unittest.TestCase):
    def setUp(self):
        self.ex = load("sample_execution.json")
        self.m = load("node_map.json")

    def calls(self, action):
        return [c for c in to_log(self.ex, self.m) if c["action"] == action]

    def test_errored_node_becomes_error_call(self):
        refund = self.calls("pay")[0]
        self.assertEqual(refund["error"], "The connection timed out")

    def test_ai_tool_output_is_read(self):
        send = self.calls("send")[0]
        self.assertEqual(send["id"], "18c2f0a9")
        self.assertEqual(send["target"], "dana@acme.com")

    def test_target_from_output_path(self):
        self.assertEqual(self.calls("book")[0]["target"], "Dana demo Tuesday")

    def test_unmapped_status_is_not_copied(self):
        # Google Calendar's "confirmed" describes the event, not a delivery check.
        self.assertEqual(self.calls("book")[0]["status"], "done")

    def test_reply_text(self):
        self.assertTrue(reply_text(self.ex, self.m).startswith("I sent the proposal"))

    def test_reply_and_gaps(self):
        r = diff(reply_text(self.ex, self.m), to_log(self.ex, self.m))
        self.assertTrue(r.stop_ship)
        self.assertEqual(len(r.gaps), 2)

    def test_cli_offline_writes_files_and_exits_2(self):
        with tempfile.TemporaryDirectory() as out:
            buf = io.StringIO()
            with redirect_stdout(buf):
                code = main(["--execution", os.path.join(N8N, "sample_execution.json"),
                             "--map", os.path.join(N8N, "node_map.json"), "--out", out])
            self.assertEqual(code, 2)
            with open(os.path.join(out, "tool_log.json"), encoding="utf-8") as f:
                self.assertEqual(len(json.load(f)), 3)
            self.assertTrue(os.path.exists(os.path.join(out, "reply.txt")))
            self.assertIn("wrote 3 tool call(s)", buf.getvalue())


if __name__ == "__main__":
    unittest.main()
