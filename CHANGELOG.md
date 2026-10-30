# Changelog

## v0.2.1 (2026-10-30)

- **LangChain callback fix.** A tool call that raised (`on_tool_error`) now keeps the attempted
  `amount` and `idempotency_key` from the tool's input, so a failed refund is logged with its amount
  as it is for successful calls. The handler also parses `input_str` when it is a Python dict repr
  (how langchain-core builds it) if `inputs` is not passed. Covered by a test against real
  langchain-core in CI.
- **Python 3.10+.** Python 3.9 is end-of-life and no longer on GitHub's hosted runners; CI now runs
  3.10–3.13 and `requires-python` is `>=3.10`.
- CI workflow added (unit tests on 3.10–3.13, a langchain-core job, and a self-test of the Action).
- Workflows and examples use `actions/checkout@v7`, `actions/setup-python@v7` and
  `actions/upload-artifact@v7` (Node 24).
- Moving major tag `v0`: `uses: SolokitStudio/say-do-diff@v0` follows the latest v0.x release.
- CLI closes the reply file after reading it (no `ResourceWarning`).
- Action metadata ready for the GitHub Marketplace (description, branding).
- No changes to grading or the CLI.

## v0.2.0 (2026-10-29)

- **GitHub Action.** `uses: SolokitStudio/say-do-diff@v0.2.0` checks one `message` + `log` pair or
  every run folder under `runs`, writes error annotations and a job summary, sets `claims`, `gaps`,
  `stop-ship` and `failed-runs` outputs, and fails per `fail-on` (`gap`, `stop-ship`, `never`).
  Optional combined JSON report. Example workflow in `examples/github-action/`.
- **LangChain / LangGraph callback** (`examples/langchain/saydo_callback.py`): records mapped tool
  calls in the Say-Do Diff log format; `handler.check(reply)` and `handler.write(path)`. Offline demo
  and tests that need no API key.
- **n8n example** (`examples/n8n`), added after v0.1.0.
- Demo GIF, ROADMAP, CONTRIBUTING, CI on Python 3.10–3.13.
- No changes to grading or the CLI.

## v0.1.0

- First release: CLI and Python API, evidence grades, six checks, JSON report, exit codes.
