# LangChain / LangGraph callback

`saydo_callback.py` is a LangChain callback handler that records the tool calls of a run in the
Say-Do Diff log format, so you can check the agent's final reply against what its tools returned.
It is a single file with no dependencies beyond the standard library (it subclasses LangChain's
`BaseCallbackHandler` when `langchain-core` is installed).

## Use

```python
from saydo_callback import SayDoCallbackHandler

handler = SayDoCallbackHandler(tool_map={
    # LangChain tool name -> how to read it
    "send_email":   {"tool": "gmail.send", "action": "send", "target": "to",
                     "id": "id", "status": "status"},
    "issue_refund": {"tool": "stripe.refund", "action": "pay", "target": "order_id",
                     "amount": "amount", "id": "refund.id", "status": "refund.status"},
})

result = agent.invoke({"messages": [("user", "Refund order 88412 and email Dana")]},
                      config={"callbacks": [handler]})
reply = result["messages"][-1].content

report = handler.check(reply)          # saydo_diff Report
print(report.to_text())
handler.write("eval/runs/refund/tool_log.json")   # for the CLI or the GitHub Action
```

The same `config={"callbacks": [handler]}` works for LangGraph graphs (for example
`create_react_agent`) and for plain `tool.invoke(...)` calls. Use one handler per run, or call
`handler.reset()` between runs.

## Tool map

| Key | Read from | Meaning |
|---|---|---|
| `action` | fixed | `send`, `create`, `update`, `delete`, `book`, `pay`, `post` or `cancel` (required) |
| `tool` | fixed | name written to the log (defaults to the LangChain tool name) |
| `target` | tool input | dotted path to the recipient, order or event name |
| `target_value` | fixed | use a fixed target instead |
| `id`, `status`, `http_status` | tool output | dotted path into the returned dict or JSON string |
| `amount`, `idempotency_key` | tool output, then input | dotted path |

Tools that are not in the map (search, lookups, other read-only tools) are not recorded.

The output can be a dict, a JSON string, or a `ToolMessage` (its `artifact` or `content` is read).
A `ToolMessage` with `status="error"` and any exception raised by the tool are logged with an
`error`, which grades S1.

## What it can and cannot tell you

- The handler records what each tool returned. If a tool returns plain text such as
  `"Email sent!"`, the call is logged as `done` with no id, which grades **S2**: the log can't back
  a past-tense "I sent ..." claim. Return the provider's object id and status from your tools to
  reach S3.
- It trusts your tool code. A tool that returns a made-up id will look like evidence.
- It does not change the reply. To check before the reply is sent, call `handler.check()` in your
  own code and decide what to do with the gaps.

## Try it without a model or API key

```bash
python3 examples/langchain/demo_offline.py
```

This replays the callback events for three tool calls (an email that was only queued, an event
with an id, a refund that timed out) and checks a reply against them. Exit code 2: the refund
claim is unsupported.

Tests: `python3 -m unittest tests.test_langchain` (the test that invokes real LangChain tools is
skipped unless `langchain-core` is installed; CI runs it).
