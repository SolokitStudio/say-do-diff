import io
import json
import os
import shutil
import sys
import tempfile
import unittest
from contextlib import redirect_stdout

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
sys.path.insert(0, os.path.join(ROOT, "action"))

from run_action import main  # noqa: E402

RUNS = os.path.join(ROOT, "examples", "ci", "runs")


def run(**env):
    buf = io.StringIO()
    with redirect_stdout(buf):
        code = main({k: v for k, v in env.items()})
    return code, buf.getvalue()


class ActionTest(unittest.TestCase):
    def setUp(self):
        self.tmp = tempfile.TemporaryDirectory()
        self.out = os.path.join(self.tmp.name, "out")
        self.summary = os.path.join(self.tmp.name, "summary")

    def tearDown(self):
        self.tmp.cleanup()

    def outputs(self):
        with open(self.out) as fh:
            return dict(line.strip().split("=", 1) for line in fh if line.strip())

    def test_runs_dir_fails_on_gap_and_sets_outputs(self):
        report = os.path.join(self.tmp.name, "r.json")
        code, text = run(SAYDO_RUNS=RUNS, GITHUB_OUTPUT=self.out, GITHUB_STEP_SUMMARY=self.summary,
                         SAYDO_JSON_REPORT=report)
        self.assertEqual(code, 1)
        self.assertEqual(self.outputs(), {"claims": "5", "gaps": "3", "stop-ship": "true", "failed-runs": "1"})
        self.assertIn("::error file=", text)
        with open(self.summary) as fh:
            s = fh.read()
        self.assertIn("| refund-timeout | 3 | 3 | yes |", s)
        self.assertIn("| refund-ok | 2 | 0 | no |", s)
        with open(report) as fh:
            self.assertEqual(len(json.load(fh)["runs"]), 2)

    def test_recursive_runs_glob(self):
        for group, name in (("a", "run-1"), ("b", "run-2")):
            destination = os.path.join(self.tmp.name, "eval", group, name)
            shutil.copytree(os.path.join(RUNS, "refund-ok"), destination)
        report = os.path.join(self.tmp.name, "r.json")
        pattern = os.path.join(self.tmp.name, "eval", "**", "run-*")
        code, _ = run(SAYDO_RUNS=pattern, SAYDO_JSON_REPORT=report, GITHUB_OUTPUT=self.out)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["claims"], "4")
        with open(report) as fh:
            runs = json.load(fh)["runs"]
        self.assertEqual(len(runs), 2)
        self.assertNotEqual(runs[0]["run"], runs[1]["run"])

    def test_glob_does_not_count_nested_runs_twice(self):
        destination = os.path.join(self.tmp.name, "eval", "run-1")
        shutil.copytree(os.path.join(RUNS, "refund-ok"), destination)
        code, _ = run(SAYDO_RUNS=os.path.join(self.tmp.name, "eval", "**"),
                      GITHUB_OUTPUT=self.out)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["claims"], "2")

    def test_unmatched_runs_glob_is_an_error(self):
        with self.assertRaisesRegex(SystemExit, "no folder"):
            run(SAYDO_RUNS=os.path.join(self.tmp.name, "missing-*"))

    def test_literal_runs_directory_with_glob_characters(self):
        destination = os.path.join(self.tmp.name, "runs[1]")
        shutil.copytree(os.path.join(RUNS, "refund-ok"), destination)
        code, _ = run(SAYDO_RUNS=destination, GITHUB_OUTPUT=self.out)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["claims"], "2")

    def test_single_clean_pair_passes(self):
        d = os.path.join(RUNS, "refund-ok")
        code, _ = run(SAYDO_MESSAGE=os.path.join(d, "reply.txt"), SAYDO_LOG=os.path.join(d, "tool_log.json"),
                      GITHUB_OUTPUT=self.out)
        self.assertEqual(code, 0)
        self.assertEqual(self.outputs()["gaps"], "0")

    def test_fail_on_modes(self):
        self.assertEqual(run(SAYDO_RUNS=RUNS, SAYDO_FAIL_ON="stop-ship")[0], 2)
        self.assertEqual(run(SAYDO_RUNS=RUNS, SAYDO_FAIL_ON="never")[0], 0)

    def test_bad_inputs(self):
        with self.assertRaises(SystemExit):
            run()
        with self.assertRaises(SystemExit):
            run(SAYDO_RUNS=RUNS, SAYDO_MESSAGE="x")
        with self.assertRaises(SystemExit):
            run(SAYDO_RUNS=RUNS, SAYDO_FAIL_ON="sometimes")
        with self.assertRaises(SystemExit):
            run(SAYDO_RUNS=self.tmp.name)


if __name__ == "__main__":
    unittest.main()
