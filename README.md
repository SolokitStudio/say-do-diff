# Say-Do Diff

[![CI](https://github.com/SolokitStudio/say-do-diff/actions/workflows/ci.yml/badge.svg)](https://github.com/SolokitStudio/say-do-diff/actions/workflows/ci.yml)

Your agent's final message says *"I sent the proposal and refunded the $45."* Your tool log says the email was only queued (HTTP 202) and the refund timed out. Say-Do Diff finds that gap.

It reads the agent's reply, pulls out each completed-action claim ("sent", "booked", "refunded", "all set"), and checks it against a JSON log of the tool calls from the same run. Every claim gets an evidence grade, and any claim whose wording is stronger than its evidence is flagged.

- Python 3.10+, standard library only, no network, no model in the loop.
- Read-only: it reports gaps. It does not change the reply.
- MIT licensed.

![Say-Do Diff flagging unsupported claims in an agent reply](docs/demo.gif)

*Real output from `examples/reply.txt` and `examples/tool_log.json`. Exit code 2 means a payment claim is unsupported.*

Run it from the CLI, from Python, [in CI as a GitHub Action](#github-action), or on logs recorded by the [LangChain callback](#langchain--langgraph) or the [n8n adapter](#n8n). It also reads the receipts that [planlock and Microsoft's Agent Governance Toolkit](#works-with-planlock-and-microsoft-agent-governance-toolkit) already write.

## Quickstart

```bash
git clone https://github.com/SolokitStudio/say-do-diff.git && cd say-do-diff
python3 -m saydo_diff --message examples/reply.txt --log examples/tool_log.json
python3 -m unittest discover -s tests
```

Optional: `pip install -e .` gives you a `saydo-diff` command.

## Example

`examples/reply.txt`:

```
Great news! I sent the proposal to dana@acme.com and booked the demo call with Dana for Tuesday.
I also refunded $45 for order 88412, and I sent all 5 invoices. Everything is taken care of.
```

`examples/tool_log.json` (abridged):

```json
[
  {"tool": "sendgrid.send", "action": "send", "target": "dana@acme.com", "http_status": 202, "status": "accepted"},
  {"tool": "calendar.create_event", "action": "book", "target": "Dana demo Tuesday", "status": "done", "id": "evt_5521"},
  {"tool": "stripe.refund", "action": "pay", "target": "order 88412", "amount": 45, "error": "timeout"},
  {"tool": "invoices.send", "action": "send", "target": "invoice 1001", "status": "done", "id": "msg_1"}
]
```

Output:

```
[GAP] sent        S2  wording needs S3, best evidence is S2
[ok ] booked      S3  supported at S3
[GAP] refunded    S1  wording needs S3, best evidence is S1
[GAP] sent        S3  partial batch: 3 of 5 reached S3
[GAP] (umbrella)  S2  umbrella claim, but these calls are below S3: sendgrid.send (S2), stripe.refund (S1)
[DUP] invoices.send send invoice 1003 logged 2 times without one idempotency key (entries [5, 6])

4 gap(s) in 5 claim(s), gap rate 80%  STOP-SHIP: a payment or delete claim is unsupported
```

(The real output also prints the sentence under each line.) Exit codes: `0` no gaps, `1` at least one gap, `2` an unsupported payment or delete claim. Add `--json` for a machine-readable report, which makes it easy to run in CI over saved transcripts.

From Python:

```python
from saydo_diff import diff

report = diff(reply_text, "runs/tool_log.json")   # a path, a JSON string, JSONL, or a list of dicts
for f in report.gaps:
    print(f.claim.verb, f.grade, f.reason)
```

## GitHub Action

Check saved agent runs on every pull request and fail the build when a reply claims more than its tool log shows.

```yaml
- uses: actions/checkout@v7
- uses: SolokitStudio/say-do-diff@v0
  with:
    runs: eval/runs            # each sub-folder holds reply.txt + tool_log.json
    fail-on: gap               # gap | stop-ship | never
    json-report: saydo-report.json
```

Or check a single pair with `message:` and `log:` instead of `runs:`. Each gap becomes an error annotation on the reply file, the job summary gets a table per run, and the step sets `claims`, `gaps`, `stop-ship` and `failed-runs` outputs. The action is a composite action that uses the runner's Python 3.10+ (`python3` on the Linux and macOS hosted runners; set the `python` input elsewhere) and installs nothing. A full workflow is in [`examples/github-action/saydo-diff.yml`](examples/github-action/saydo-diff.yml), and [`examples/ci/runs`](examples/ci/runs) has a passing and a failing run to try it on.

The action checks logs you already have; producing them (replaying your agent against fixtures, or saving production runs) is up to your pipeline.

## LangChain / LangGraph

[`examples/langchain`](examples/langchain) has a callback handler that records tool calls into the log format above while your agent runs:

```python
from saydo_callback import SayDoCallbackHandler

handler = SayDoCallbackHandler({"send_email": {"action": "send", "target": "to", "id": "id", "status": "status"}})
result = agent.invoke(inputs, config={"callbacks": [handler]})
print(handler.check(final_reply).to_text())
handler.write("eval/runs/run-001/tool_log.json")
```

Only tools you list are recorded, and a tool that returns plain text with no id grades S2, so the log can't back a past-tense claim on its own. `python3 examples/langchain/demo_offline.py` runs it without a model or API key.

## n8n

[`examples/n8n`](examples/n8n) turns a saved n8n execution into a tool log and checks the AI Agent's reply against it. It grades any node you list in a node map (the sample maps Gmail, Google Calendar and an HTTP Request refund), including AI Agent tool sub-nodes, and reads the execution from the n8n API or a saved JSON file. A sample execution and node map are included, and the README there covers run steps and limits.

## Works with planlock and Microsoft Agent Governance Toolkit

If your agent already runs behind [planlock](https://github.com/JosephCurwin/planlock) or the [Microsoft Agent Governance Toolkit](https://github.com/microsoft/agent-governance-toolkit) (AGT), you don't need to log anything new. Point Say-Do Diff at the receipts those tools already write:

```bash
# planlock: the session log, plus the job packets it writes next to it
python3 -m saydo_diff -m reply.txt -l $LOG_ROOT/session_events.log --from planlock --map tool_map.json --plan

# AGT: a FileAuditSink JSONL, AuditLog.export(), export_cloudevents(), or the TS/Go SDK audit export
AGT_AUDIT_SECRET=... python3 -m saydo_diff -m reply.txt -l audit.jsonl --from agt
```

`--from auto` detects the format. The same option is available in the GitHub Action (`from: planlock`, `map: tool_map.json`). `--emit-log out.json` writes the imported calls in the plain [tool log format](#tool-log-format), and `--require-integrity` exits 3 if an audit chain doesn't verify.

**Tool names to actions.** Neither tool records what kind of action a call was, so Say-Do Diff infers it from the tool name: `send_email` → send, `issue_refund` → pay, `crm.customers.delete` → delete, and `get_*`/`lookup_*`/`read_*` count as lookups. Anything else should go in a map, a JSON object such as `{"submitToNetwork": "pay", "riskScore": "read"}`. Without one, a claim like "paid" can't find its call, and the report says so.

### planlock

planlock records each tool call that passes its plan binding as a `tool_call` row in `session_events.log`. It also records `permission_denied`, `binding_violation` (argument or pin check), `tool_call_error` and `upstream_failure`. Each step's audit verdict (`PASSED`/`FAILED`) is logged too. These rows have no arguments and no result. During `PATCH /feature/implement/active` steps, planlock's env-inject adapter also writes a job packet (`outbox/<txn_id>.job.json`) listing each call's arguments in order. Say-Do Diff matches those packets to the calls: it looks in the logged `outbox_dir`, then `<log dir>/vm_io/outbox`, or the folder you pass with `--planlock-outbox`. Without packets, targets come from the plan's `eq` constraints.

| planlock row | Grade |
|---|---|
| `tool_call` in a step whose audit `PASSED` | S3: planlock checked that the bound call returned without `isError` |
| `tool_call` elsewhere | S2 |
| `tool_call_error` | S1 |
| `tool_call` + `upstream_failure` for that tool | F |
| `permission_denied`, `binding_violation` | F, unless a later call to the same tool succeeded (a retry within the plan) |

Tools allowed on an investigation route (`GET /bug/diagnose`), plus those listed in a profile you pass with `--planlock-profile`, count as lookups. planlock's own `agreement_submit` calls are skipped.

Because planlock holds the approved plan, `--plan` adds a **plan → done → claim** view. Each step shows what was approved, what ran, and what the reply said about it. Here it is on the [sample run](examples/planlock), where the reply claims the ledger entry that planlock blocked (abridged):

```
PLAN 1: Pay recipient vendor-20417 25 EUR from acc_main and post the ledger entry
  status on hold, plan_hash 91e620c4269c, approved by human, at 2026-10-31T01:28:43.202Z

  Step 2 pay: IMPLEMENTED (audit PASSED)  -> planned, done, claimed
    plan:   Submit the 25 EUR payout to vendor-20417
            submitToNetwork  recipient = 'vendor-20417', amount 1..25, max 1 call(s)
    done:   F  submitToNetwork (arguments not logged)  - planlock binding_violation: submitToNetwork.recipient must equal the approved value; superseded by a later submitToNetwork call
            S3 submitToNetwork recipient=vendor-20417 amount=25
    claim:  [ok] paid S3  "I reserved €25 on acc_main, paid €25 to vendor-20417, and posted the ledger entry ..."

  Step 3 ledger: ON HOLD (audit FAILED)  -> CLAIMED BUT NOT DONE
    hold reason: Step did not execute any of its approved tools (postLedger).
    plan:   Post the ledger entry for the vendor-20417 payout
            postLedger  entry = 'payout vendor-20417', amount = 25, max 1 call(s), pin getAccountBalance.currency == 'USD'
    done:   F  postLedger entry=payout vendor-20417 amount=25  - planlock binding_violation: pin_mismatch (expected 'USD', got 'EUR')
    claim:  [GAP] posted F  "I reserved €25 on acc_main, paid €25 to vendor-20417, and posted the ledger entry ..."
```

Step flags are `planned, done, claimed`, `CLAIMED BUT NOT DONE`, `claim stronger than evidence`, `done, not mentioned in the reply` and `not run, not claimed`. Overridden steps (`FAILURE_OVERRIDDEN_AND_PROMOTED`), aborted plans and correction plans are shown as such. With `--json --plan` you get the same view as data.

### Microsoft Agent Governance Toolkit

AGT is a policy layer, so most of what it logs is a **decision** made before a tool runs. Say-Do Diff grades by what each row proves:

| AGT row | Grade |
|---|---|
| `tool_invocation` `complete` (Agent Framework middleware) with an id in `result_preview` | S3 |
| `tool_invocation` `complete` with no id, BaseAgent `result_success: true` | S2 |
| `tool_invocation` `start` with no `complete` (the tool raised) | S1 |
| a policy `allow` (`policy_evaluation`, `mcp_tool_call`, MCP gateway `allowed`, SDK `decision: allow`) | S1: allowed, not shown to have run |
| `review`/`escalate`, gateway approval `pending` | S1, pending |
| `deny`, `tool_blocked`, outcome `failure` | F |

It reads:
- agentmesh `FileAuditSink` JSONL. It recomputes each row's `content_hash`, checks the `previous_hash` chain and, if `AGT_AUDIT_SECRET` (or `--agt-secret-env`) holds the sink's key, checks the HMAC `signature`.
- `AuditLog.export()` JSON and JSON/JSONL of `AuditEntry` rows (`entry_hash` and the chain are recomputed).
- `export_cloudevents()` (hash links checked).
- agent_os `MCPGateway` audit entries, `GovernanceAuditLogger` JSONL, `BaseAgent.get_audit_log()`.
- the TypeScript SDK's `AuditLogger.exportJSON()` (hash chain recomputed) and Go `AuditLogger` entries (links checked).

A chain that doesn't verify is reported as a warning with the first bad entry.

The samples in [`examples/agt`](examples/agt) were written by AGT itself: `agent-governance-toolkit-core` 5.0.0 for Python and `@microsoft/agent-governance-sdk` 5.0.0 for TypeScript. The generators are included. In the main sample, an Agent Framework support run refunds an order, starts an email that raises, is blocked from deleting an account and posts to Slack. The reply claims all four:

```
[ok ] refunded    S3  supported at S3
[GAP] emailed     S1  wording needs S3, best evidence is S1 (AGT logged the tool start but no completion (the tool raised or the run stopped))
[GAP] deleted     F   wording needs S3, best evidence is F (AGT policy denied (Tool 'delete_customer_account' is in denied_tools))
[ok ] posted      S3  supported at S3
[GAP] (umbrella)  S1  umbrella claim, but these calls are below S3: delete_customer_account (F), send_email (S1)

3 gap(s) in 5 claim(s), gap rate 60%  STOP-SHIP: a payment or delete claim is unsupported
```

If a log has only decisions (the agentmesh MCP proxy, `govern()`, the SDK audit logger), every past-tense claim will be a gap, and the report warns you. To back such claims, log tool results as well, for example with the Agent Framework middleware's `tool_invocation` start/complete.

## Evidence grades

| Grade | What the tool log shows | Wording it supports |
|---|---|---|
| S0 | No call for this claim | "I did not ..." |
| S1 | Call made, then error, timeout or unknown result | "I tried to ..., not confirmed" |
| S2 | Accepted or queued (HTTP 202, `pending`), or "done" with no object id | "I queued / requested ..." |
| S3 | Done, with an object id | "I sent / booked / paid ..." |
| S4 | Verified afterwards (delivered webhook, read-back) | "... and it was delivered / confirmed" |
| F | Provider reported failure (4xx/5xx, bounce) | "I tried to ..., but it failed" |

Past-tense claims need S3. "Delivered", "confirmed" and "verified" need S4.

## Tool log format

A JSON array (or JSONL, or `{"calls": [...]}`). Per entry, all fields are optional except `action`:

| Field | Meaning |
|---|---|
| `action` | one of `send`, `create`, `update`, `delete`, `book`, `pay`, `post`, `cancel` |
| `tool` | tool name, used in the report |
| `target` | string or object: recipient, order number, event name. Claims are matched against it |
| `status` | `accepted`, `queued`, `pending`, `done`, `delivered`, `failed`, `bounced`, `timeout`, ... |
| `http_status` | 202 is S2; 2xx with an `id` is S3; 4xx/5xx is F |
| `id` | object id from the provider (needed for S3) |
| `amount` | checked against the dollar amount in the claim |
| `error` | any value makes the call S1 |
| `idempotency_key`, `repeat_ok` | used by the duplicate check |
| `grade` | set `S0`–`S4` or `F` yourself to override the mapping |

## What it checks

1. **Unsupported claim**: the wording needs a higher grade than the best matching call.
2. **Wrong target**: the claim names an email, id or quoted name that no call of that action has.
3. **Partial batch**: "sent all 5" when only 3 distinct targets reached S3 (retries are not counted twice).
4. **Money mismatch**: the amount in the claim differs from the amount in the log.
5. **Umbrella**: "all set", "taken care of" or ✅ while any call in the run is below S3, or the run has no calls.
6. **Duplicates**: the same tool, action and target logged twice without a shared idempotency key.

## Limits

- **English only, small verb list.** Phrasings outside the built-in list are not detected, so a clean report does not prove the reply is honest. Extend `VERBS` and `UMBRELLA` in `saydo_diff/core.py`.
- **The log is trusted as written.** For plain tool logs, nothing here proves the log came from your tool layer and not from the model; if the agent can write to the log, it can fake evidence. AGT hash chains and HMAC signatures are checked when present, and planlock logs come from the proxy, not the model, but a log that is fully rewritten with a valid chain (or without the secret) still can't be told apart.
- **Imported logs prove less than they look.** planlock records no tool results or object ids, and most AGT integrations record the policy decision rather than the outcome. The grades above reflect that, so expect more gaps than with a log you write yourself.
- **Matching is literal.** Nicknames and misspellings do not match a target. That errs toward flagging.
- **Some false positives.** "The file you sent" is skipped, but other second-hand phrasings can be read as claims.
- **Advisory only.** It reports; it does not stop or fix a reply.

## What the full Say-Do Gate adds

This repo is the free, read-only checker. The paid [Say-Do Gate](https://solokit33.gumroad.com/l/hduxqf) ($49, Solokit Studio) runs in line, before the reply is sent, and adds:

- **Signed receipts.** Your tool layer writes HMAC-signed receipts with a key the model never sees, so the evidence can't be forged by the agent.
- **Clause-level rewrite.** Only the false clause is changed ("I queued the proposal to dana@acme.com; it is not delivered yet"), and the rest of the reply is left alone.
- **Holds.** Unconfirmed payments and deletes stop the reply for escalation.
- **Late confirmations.** A webhook that lands after the reply produces one status line, with an alert if nothing confirms in 30 minutes.
- **Say-Do Scoreboard.** A weekly gap rate per tool, plus a coverage report of sentences the lexicon could not read.
- **Adapters** for OpenAI and Anthropic tool calls, an HTTP endpoint for n8n, Zapier and Make, Guardrails AI and Langfuse evaluators.

The trade-off: the Gate needs you to wrap your tools so they write receipts, which is more setup than pointing this checker at a log you already have.

If you'd rather audit by hand first, the free [Say-Do Diff checklist](https://solokit33.gumroad.com/l/bkgskz) is a 10-minute paper version of the same check. The Gate is also included in the [Operations Suite](https://solokit33.gumroad.com/l/operations-trio) ($149).

## Contributing

See [CONTRIBUTING.md](CONTRIBUTING.md) and the [roadmap](ROADMAP.md). Questions and ideas go in [Discussions](https://github.com/SolokitStudio/say-do-diff/discussions); small starter tasks are labelled [good first issue](https://github.com/SolokitStudio/say-do-diff/labels/good%20first%20issue).

## License

MIT. See `LICENSE`.
