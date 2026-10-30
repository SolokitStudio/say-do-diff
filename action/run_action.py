"""Entry point for the Say-Do Diff GitHub Action. Standard library only.

Reads its settings from SAYDO_* environment variables (set by action.yml), checks one
message/log pair or every run folder under SAYDO_RUNS, prints the text report, writes a
job summary and error annotations, sets step outputs, and exits non-zero per SAYDO_FAIL_ON.
"""
import glob
import json
import os
import sys

from saydo_diff import diff


def _esc(s):
    return str(s).replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")


def pairs(env):
    msg, log, runs = env.get("SAYDO_MESSAGE", ""), env.get("SAYDO_LOG", ""), env.get("SAYDO_RUNS", "")
    if runs:
        if msg or log:
            raise SystemExit("say-do-diff: set either `runs` or `message` + `log`, not both")
        rf, lf = env.get("SAYDO_REPLY_FILE") or "reply.txt", env.get("SAYDO_LOG_FILE") or "tool_log.json"
        literal = os.path.isdir(runs)
        roots = [runs] if literal else glob.glob(runs, recursive=True)
        found = []
        seen = set()
        for runs_root in roots:
            for root, _dirs, files in os.walk(runs_root):
                key = os.path.abspath(root)
                if rf in files and lf in files and key not in seen:
                    name = os.path.relpath(root, runs) if literal else os.path.normpath(root)
                    found.append((name, os.path.join(root, rf), os.path.join(root, lf)))
                    seen.add(key)
        if not found:
            raise SystemExit(f"say-do-diff: no folder under {runs!r} has both {rf} and {lf}")
        return sorted(found)
    if not (msg and log):
        raise SystemExit("say-do-diff: set `message` and `log`, or `runs`")
    return [(os.path.basename(msg), msg, log)]


def main(env=None) -> int:
    env = dict(os.environ if env is None else env)
    fail_on = (env.get("SAYDO_FAIL_ON") or "gap").strip().lower()
    if fail_on not in ("gap", "stop-ship", "never"):
        raise SystemExit(f"say-do-diff: fail-on must be gap, stop-ship or never, not {fail_on!r}")

    source = (env.get("SAYDO_FROM") or "native").strip().lower()
    if source not in ("native", "planlock", "agt", "auto"):
        raise SystemExit(f"say-do-diff: from must be native, planlock, agt or auto, not {source!r}")
    opts = {}
    if source != "native" or env.get("SAYDO_MAP"):
        from saydo_diff.adapters import load_mapping
        opts["mapping"] = load_mapping(env.get("SAYDO_MAP") or None)
        source = "auto" if source == "native" else source

    results, claims, gaps, failed, stop = [], 0, 0, 0, False
    summary = ["## Say-Do Diff", "", "| Run | Claims | Gaps | Stop-ship |", "|---|---|---|---|"]
    for name, msg_path, log_path in pairs(env):
        with open(msg_path, encoding="utf-8") as fh:
            text = fh.read()
        report = diff(text, log_path) if source == "native" else diff(text, log_path, source, **opts)
        print(f"== {name}\n{report.to_text()}\n")
        claims += len(report.findings)
        gaps += len(report.gaps)
        failed += bool(report.gaps)
        stop = stop or report.stop_ship
        summary.append(f"| {name} | {len(report.findings)} | {len(report.gaps)} | "
                       f"{'yes' if report.stop_ship else 'no'} |")
        for f in report.gaps:
            print(f"::error file={msg_path},title=Say-Do Diff: unsupported '{_esc(f.claim.verb)}'::"
                  f"{_esc(f.reason)}: {_esc(f.claim.sentence)}")
        results.append({"run": name, "message": msg_path, "log": log_path, **report.to_dict()})

    summary += ["", f"**{gaps} gap(s) in {claims} claim(s) across {len(results)} run(s).**"]
    if env.get("GITHUB_STEP_SUMMARY"):
        with open(env["GITHUB_STEP_SUMMARY"], "a", encoding="utf-8") as fh:
            fh.write("\n".join(summary) + "\n")
    if env.get("SAYDO_JSON_REPORT"):
        with open(env["SAYDO_JSON_REPORT"], "w", encoding="utf-8") as fh:
            json.dump({"claims": claims, "gaps": gaps, "stop_ship": stop, "runs": results}, fh, indent=2)
    if env.get("GITHUB_OUTPUT"):
        with open(env["GITHUB_OUTPUT"], "a", encoding="utf-8") as fh:
            for k, v in (("claims", claims), ("gaps", gaps), ("stop-ship", str(stop).lower()),
                         ("failed-runs", failed)):
                fh.write(f"{k}={v}\n")

    if fail_on == "gap" and gaps:
        return 1
    if fail_on == "stop-ship" and stop:
        return 2
    return 0


if __name__ == "__main__":
    sys.exit(main())
