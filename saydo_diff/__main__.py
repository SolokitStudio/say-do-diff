"""CLI: python -m saydo_diff --message reply.txt --log tool_log.json [--from planlock|agt|auto] [--json]

Exit codes: 0 no gaps, 1 at least one gap, 2 a payment or delete claim is unsupported.
"""
import argparse
import json
import sys

from .core import diff


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="saydo_diff", description=__doc__.splitlines()[0])
    p.add_argument("--message", "-m", required=True, help="file with the agent's final reply ('-' for stdin)")
    p.add_argument("--log", "-l", required=True,
                   help="tool-call log: say-do-diff JSON/JSONL, a planlock session_events.log, or an AGT audit log")
    p.add_argument("--from", dest="source", default="native", choices=["native", "planlock", "agt", "auto"],
                   help="log format (default: native; auto sniffs the file)")
    p.add_argument("--map", help="JSON object of tool name -> action (send, pay, book, ...; 'read' for lookups)")
    p.add_argument("--plan", action="store_true", help="also print the plan -> done -> claim view (planlock)")
    p.add_argument("--planlock-outbox", help="folder with planlock job packets (<txn_id>.job.json)")
    p.add_argument("--planlock-profile", help="planlock domain profile; its investigation_read_only_tools are lookups")
    p.add_argument("--agt-secret-env", default="AGT_AUDIT_SECRET",
                   help="env var holding the FileAuditSink HMAC secret, to check signatures (default: %(default)s)")
    p.add_argument("--require-integrity", action="store_true",
                   help="exit 3 if an imported audit log's hash chain or signatures do not verify")
    p.add_argument("--emit-log", help="write the imported calls as a say-do-diff JSON log to this path")
    p.add_argument("--json", action="store_true", help="print a JSON report")
    a = p.parse_args(argv)
    if a.message == "-":
        text = sys.stdin.read()
    else:
        with open(a.message, encoding="utf-8") as f:
            text = f.read()

    opts = {}
    if a.source != "native" or a.map:
        from .adapters import load_mapping
        opts["mapping"] = load_mapping(a.map)
        if a.source == "native":
            a.source = "auto"
    if a.planlock_outbox:
        opts["outbox"] = a.planlock_outbox
    if a.planlock_profile:
        opts["profile"] = a.planlock_profile
    if a.agt_secret_env:
        opts["secret_env"] = a.agt_secret_env
    try:
        report = diff(text, a.log, a.source, **opts) if a.source != "native" else diff(text, a.log)
    except (OSError, ValueError) as e:
        print(f"saydo_diff: {e}", file=sys.stderr)
        return 4

    if a.emit_log and report.imported is not None:
        with open(a.emit_log, "w", encoding="utf-8") as fh:
            json.dump({"calls": report.imported.calls}, fh, indent=2, default=str)
    if a.json:
        d = report.to_dict()
        if a.plan:
            from .planview import build
            d["plans"] = build(report)
        print(json.dumps(d, indent=2, default=str))
    else:
        print(report.to_text())
        if a.plan:
            from .planview import render
            print("\n" + render(report))
    integ = ((report.imported.meta or {}).get("integrity") or {}) if report.imported is not None else {}
    if a.require_integrity and integ.get("ok") is not True:
        print(f"saydo_diff: audit log integrity not verified: {integ.get('detail', 'no hash chain')}", file=sys.stderr)
        return 3
    if report.stop_ship:
        return 2
    return 1 if report.gaps else 0


if __name__ == "__main__":
    sys.exit(main())
