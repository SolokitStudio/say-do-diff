# Changelog

## v0.3.0 (2026-10-30)

- **Reads planlock and Microsoft Agent Governance Toolkit receipts.** `--from planlock|agt|auto`
  (and `from:` / `map:` in the GitHub Action) imports the logs these tools already write. No new
  logging is needed.
  - **planlock**: `session_events.log` rows (`tool_call`, `tool_call_error`, `permission_denied`,
    `binding_violation`, `upstream_failure`, audit verdicts, plan lifecycle events) plus the
    env-inject job packets (`outbox/<txn_id>.job.json`) for arguments. Calls in a step whose audit
    passed grade S3; blocked attempts that a later call to the same tool replaced are marked
    superseded.
  - **AGT**: agentmesh `FileAuditSink` JSONL, with content hashes and the chain recomputed and HMAC
    signatures checked when `AGT_AUDIT_SECRET` is set. Also `AuditLog.export()`,
    `export_cloudevents()`, agent_os `MCPGateway` / `GovernanceAuditLogger` / `BaseAgent` audit
    logs, and the TypeScript and Go SDK audit exports. Policy decisions (allow/deny/review) are
    graded apart from recorded outcomes: an `allow` alone is S1.
  - Tool names map to actions by keyword, or through a `--map` JSON file.
- **Plan → done → claim view** (`--plan`, also in `--json`). For planlock runs it shows each
  approved step's constraints, the calls that ran and the reply's claims about it, with a flag
  such as `CLAIMED BUT NOT DONE`.
- `--emit-log` writes the imported calls as a plain say-do-diff log. `--require-integrity` exits
  3 when an audit chain doesn't verify.
- Sample receipts written by planlock v0.7.0, AGT Python 5.0.0 and the AGT TypeScript SDK 5.0.0 are
  in `examples/planlock` and `examples/agt`. A new CI job re-checks them against the real
  toolkit.
- Core: an umbrella claim ("all set") is now a gap when the run has only read-only lookups. Lookup
  and superseded calls are skipped by the umbrella and duplicate checks. A gap reason now includes
  the best call's note.
- Python API: `saydo_diff.import_log()`, `detect()`, `load_mapping()`, and
  `diff(message, log, source, mapping=...)`.

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
