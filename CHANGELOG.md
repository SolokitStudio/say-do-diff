# Changelog

## v0.2.0 (2026-10-29)

- **GitHub Action.** `uses: SolokitStudio/say-do-diff@v0.2.0` checks one `message` + `log` pair or
  every run folder under `runs`, writes error annotations and a job summary, sets `claims`, `gaps`,
  `stop-ship` and `failed-runs` outputs, and fails per `fail-on` (`gap`, `stop-ship`, `never`).
  Optional combined JSON report. Example workflow in `examples/github-action/`.
- **LangChain / LangGraph callback** (`examples/langchain/saydo_callback.py`): records mapped tool
  calls in the Say-Do Diff log format; `handler.check(reply)` and `handler.write(path)`. Offline demo
  and tests that need no API key.
- **n8n example** (`examples/n8n`), added after v0.1.0.
- Demo GIF, ROADMAP, CONTRIBUTING, CI on Python 3.9–3.13.
- No changes to grading or the CLI.

## v0.1.0

- First release: CLI and Python API, evidence grades, six checks, JSON report, exit codes.
