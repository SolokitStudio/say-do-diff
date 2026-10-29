# Say-Do Diff

Your agent's final message says *"I sent the proposal and refunded the $45."* Your tool log says the email was only queued (HTTP 202) and the refund timed out. Say-Do Diff finds that gap.

It reads the agent's reply, pulls out each completed-action claim ("sent", "booked", "refunded", "all set"), and checks it against a JSON log of the tool calls from the same run. Every claim gets an evidence grade, and any claim whose wording is stronger than its evidence is flagged.

- Python 3.9+, standard library only, no network, no model in the loop.
- Read-only: it reports gaps. It does not change the reply.
- MIT licensed.

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

## n8n

[`examples/n8n`](examples/n8n) turns a saved n8n execution into a tool log and checks the AI Agent's reply against it. It grades any node you list in a node map (the sample maps Gmail, Google Calendar and an HTTP Request refund), including AI Agent tool sub-nodes, and reads the execution from the n8n API or a saved JSON file. A sample execution and node map are included, and the README there covers run steps and limits.

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
- **The log is trusted as written.** Nothing here proves the log came from your tool layer and not from the model. If the agent can write to the log, it can fake evidence.
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

## License

MIT. See `LICENSE`.
