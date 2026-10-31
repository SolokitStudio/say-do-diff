"""Cross-check the AGT importer against the real toolkit (needs: pip install agent-governance-toolkit-core).

Not part of the stdlib unit tests; CI runs it in its own job. It checks that
1. the committed fixtures verify with AGT's own integrity checks,
2. say-do-diff's independent hash/signature checks agree, including on a tampered copy,
3. fixtures freshly generated with the installed toolkit grade the same as the committed ones.
"""
import json
import os
import shutil
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
sys.path.insert(0, ROOT)
AGT_DIR = os.path.join(ROOT, "examples", "agt")
SECRET = b"saydo-diff-fixture-secret"

from agentmesh.governance.audit import AuditEntry  # noqa: E402
from agentmesh.governance.audit_backends import FileAuditSink  # noqa: E402

from saydo_diff import diff  # noqa: E402
from saydo_diff.adapters import import_log  # noqa: E402


def agt_verifies(path):
    with tempfile.TemporaryDirectory() as d:
        p = os.path.join(d, "audit.jsonl")
        shutil.copy(path, p)
        sink = FileAuditSink(path=p, secret_key=SECRET, max_file_size=0)
        ok, err = sink.verify_integrity()
        sink.close()
        return ok, err


def grades(path):
    with open(os.path.join(AGT_DIR, "reply.txt"), encoding="utf-8") as fh:
        r = diff(fh.read(), path, "agt")
    return [(f.claim.verb, f.status, f.grade) for f in r.findings]


def main():
    os.environ["AGT_AUDIT_SECRET"] = SECRET.decode()
    for name in ("maf_audit.jsonl", "proxy_audit.jsonl"):
        path = os.path.join(AGT_DIR, name)
        ok, err = agt_verifies(path)
        assert ok, (name, err)
        integ = import_log(path, "agt").meta["integrity"]
        assert integ["ok"] is True and "HMAC" in integ["detail"], integ
        # tamper: change the data of row 3; both sides must flag it
        with tempfile.TemporaryDirectory() as d:
            t = os.path.join(d, name)
            rows = [json.loads(x) for x in open(path, encoding="utf-8")]
            rows[2]["data"]["tampered"] = True
            with open(t, "w", encoding="utf-8") as fh:
                fh.write("".join(json.dumps(r, sort_keys=True) + "\n" for r in rows))
            assert agt_verifies(t)[0] is False
            assert import_log(t, "agt").meta["integrity"]["ok"] is False
        print(f"ok  {name}: AGT and say-do-diff agree the chain verifies, and both catch tampering")

    with open(os.path.join(AGT_DIR, "maf_export.json"), encoding="utf-8") as fh:
        export = json.load(fh)
    for e in export["entries"]:
        assert AuditEntry.model_validate(e).verify_hash(), e["entry_id"]
    assert import_log(os.path.join(AGT_DIR, "maf_export.json"), "agt").meta["integrity"]["ok"] is True
    print("ok  maf_export.json: every AuditEntry.verify_hash() passes")

    expected = grades(os.path.join(AGT_DIR, "maf_audit.jsonl"))
    with tempfile.TemporaryDirectory() as d:
        subprocess.run([sys.executable, "-W", "ignore", os.path.join(AGT_DIR, "make_fixtures.py"), "--out", d],
                       check=True)
        for name in ("maf_audit.jsonl", "maf_export.json", "maf_cloudevents.json"):
            got = grades(os.path.join(d, name))
            assert got == expected, (name, got, expected)
    print(f"ok  fresh fixtures from the installed toolkit grade the same: {expected}")


if __name__ == "__main__":
    main()
