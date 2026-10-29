"""CLI: python -m saydo_diff --message reply.txt --log tool_log.json [--json]

Exit codes: 0 no gaps, 1 at least one gap, 2 a payment or delete claim is unsupported.
"""
import argparse
import json
import sys

from .core import diff


def main(argv=None) -> int:
    p = argparse.ArgumentParser(prog="saydo_diff", description=__doc__.splitlines()[0])
    p.add_argument("--message", "-m", required=True, help="file with the agent's final reply ('-' for stdin)")
    p.add_argument("--log", "-l", required=True, help="JSON or JSONL tool-call log")
    p.add_argument("--json", action="store_true", help="print a JSON report")
    a = p.parse_args(argv)
    text = sys.stdin.read() if a.message == "-" else open(a.message, encoding="utf-8").read()
    report = diff(text, a.log)
    print(json.dumps(report.to_dict(), indent=2) if a.json else report.to_text())
    if report.stop_ship:
        return 2
    return 1 if report.gaps else 0


if __name__ == "__main__":
    sys.exit(main())
