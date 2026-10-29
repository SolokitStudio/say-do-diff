# n8n adapter

Turns one n8n execution into a Say-Do Diff tool log, then checks the AI Agent's final reply against it. Standard library only.

## Files

| File | What it is |
|---|---|
| `n8n_to_log.py` | the adapter: reads an execution, writes `tool_log.json` and `reply.txt`, runs the check |
| `node_map.json` | which node holds the agent's reply, and which nodes are actions |
| `sample_execution.json` | a saved execution: Gmail send and Calendar event succeed, the refund HTTP Request times out |

## Run it

Offline, on the sample (from the repo root):

```bash
cd examples/n8n
python3 n8n_to_log.py --execution sample_execution.json --map node_map.json --out /tmp/n8n-run
```

Output:

```
wrote 3 tool call(s) and the reply to /tmp/n8n-run
[ok ] sent        S3  supported at S3
[ok ] booked      S3  supported at S3
[GAP] refunded    S1  wording needs S3, best evidence is S1
[GAP] (umbrella)  S1  umbrella claim, but these calls are below S3: stripe.refund (S1)

2 gap(s) in 4 claim(s), gap rate 50%  STOP-SHIP: a payment or delete claim is unsupported
```

Exit codes match the main CLI: `0` no gaps, `1` gaps, `2` an unsupported payment or delete claim.

On your own instance:

1. In n8n, create an API key (Settings > n8n API).
2. Edit `node_map.json` for your workflow. `reply.node` is the node that holds the agent's final reply, and `reply.field` is the field in its output. Under `nodes`, add each node that performs an action, with its `action` (`send`, `book`, `pay`, ...) and dotted paths into that node's first output item for `id`, `status`, `http_status`, `amount` and `target`. Use `target_value` for a fixed target.
3. Run it on an execution id:

```bash
N8N_URL=https://your-n8n.example.com N8N_API_KEY=... \
  python3 n8n_to_log.py --id 1234 --map node_map.json --out runs/1234
```

You can also save `GET /api/v1/executions/<id>?includeData=true` yourself and pass it with `--execution`.

## How node runs are graded

- A node run with `executionStatus: "error"` becomes a call with `error` (S1).
- A successful run with a provider id is S3. Without an id it is S2, because n8n only records that the node ran.
- Map `status` only when the provider's field really means delivered or confirmed. Google Calendar's `"status": "confirmed"` describes the event, not a delivery check, so the sample map leaves it out.
- Tool sub-nodes of an AI Agent write their output under `ai_tool`. The adapter reads both `main` and `ai_tool`.

## Limits

- **Targets come from your map.** n8n does not store a resolved recipient for most send nodes, so `target_value` is often a fixed string. The wrong-target check is only as good as the map.
- **Executions must be saved with data.** Turn on the workflow's "Save successful production executions" setting (or the instance default), or there is nothing to read.
- **First output item only.** A node that returns several items is graded on the first one. Nodes that run several times are logged once per run.
- **After the fact.** This checks a finished execution; it does not stop the reply from going out.

## Tests

From the repo root: `python3 -m unittest discover -s tests` (the adapter's tests are in `tests/test_n8n.py`).
