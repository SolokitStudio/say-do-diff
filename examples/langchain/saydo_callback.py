"""LangChain / LangGraph callback that records tool calls in the Say-Do Diff log format.

    from saydo_callback import SayDoCallbackHandler

    handler = SayDoCallbackHandler(tool_map={
        "send_email":  {"action": "send", "target": "to", "id": "message_id", "status": "status"},
        "issue_refund": {"action": "pay", "target": "order_id", "amount": "amount", "id": "refund_id"},
    })
    result = agent.invoke({"messages": [...]}, config={"callbacks": [handler]})
    report = handler.check(final_reply_text)    # a saydo_diff Report
    handler.write("runs/tool_log.json")         # or save the log and run the CLI / GitHub Action

Only tools listed in tool_map are recorded; read-only tools (search, lookups) are ignored.
Each map entry names where to read fields from:
  action        required; one of send, create, update, delete, book, pay, post, cancel
  tool          name written to the log (defaults to the LangChain tool name)
  target        input key for the recipient / order / event name ("target_value" for a fixed string)
  id, status, http_status, amount, idempotency_key
                dotted path into the tool's output (a dict, a JSON string, or a ToolMessage),
                falling back to the tool's input for amount, idempotency_key and target

The handler only records what the tool returned. If the tool returns plain text with no id or
status, the call is logged as "done" without an id, which grades S2: the log cannot back a
past-tense claim. Make your tools return the provider's id and status to reach S3.

Works with or without langchain-core installed (it subclasses BaseCallbackHandler when available).
"""
import ast
import json
import os
import sys

_ROOT = os.path.dirname(os.path.dirname(os.path.dirname(os.path.abspath(__file__))))
if os.path.isdir(os.path.join(_ROOT, "saydo_diff")) and _ROOT not in sys.path:
    sys.path.insert(0, _ROOT)

try:  # pragma: no cover - depends on the environment
    from langchain_core.callbacks import BaseCallbackHandler as _Base
except ImportError:  # keep the example usable (and testable) without LangChain
    _Base = object

_OUTPUT_FIELDS = ("id", "status", "http_status", "amount", "idempotency_key")


def _dig(obj, path):
    for part in path.split(".") if path else []:
        if not isinstance(obj, dict) or part not in obj:
            return None
        obj = obj[part]
    return obj


def _as_dict(value):
    """Best effort: turn a tool input or output into a dict."""
    if isinstance(value, dict):
        return value
    for attr in ("artifact", "content"):  # ToolMessage / Command-like objects
        inner = getattr(value, attr, None)
        if inner is not None and not isinstance(value, (str, bytes)):
            d = _as_dict(inner)
            if d:
                return d
    if isinstance(value, str):
        try:
            parsed = json.loads(value)
        except ValueError:
            # LangChain passes input_str as str(dict), a Python repr rather than JSON.
            try:
                parsed = ast.literal_eval(value)
            except (ValueError, SyntaxError, MemoryError, RecursionError):
                return {}
        return parsed if isinstance(parsed, dict) else {}
    return {}


_INPUT_FALLBACK_FIELDS = ("amount", "idempotency_key")


def _fill_from_inputs(spec, args, entry):
    """Copy amount / idempotency_key from the tool's input when the output did not supply them."""
    for field in _INPUT_FALLBACK_FIELDS:
        path = spec.get(field)
        if path and field not in entry:
            val = _dig(args, path)
            if val is not None:
                entry[field] = val


class SayDoCallbackHandler(_Base):
    """Collects mapped tool calls into a list of Say-Do Diff log entries (self.calls)."""

    raise_error = False

    def __init__(self, tool_map: dict):
        if _Base is not object:
            super().__init__()
        self.tool_map = tool_map
        self.calls = []
        self._pending = {}  # run_id -> (spec, inputs, entry)

    # ------------------------------------------------------------------ LangChain hooks
    def on_tool_start(self, serialized, input_str, *, run_id=None, inputs=None, **kwargs):
        name = (serialized or {}).get("name") or kwargs.get("name") or ""
        spec = self.tool_map.get(name)
        if spec is None:
            return
        # langchain-core passes the parsed arguments as `inputs`; input_str is str(dict).
        args = inputs if isinstance(inputs, dict) else _as_dict(input_str)
        entry = {"tool": spec.get("tool", name), "action": spec["action"]}
        if "target_value" in spec:
            entry["target"] = spec["target_value"]
        elif spec.get("target"):
            t = _dig(args, spec["target"])
            if t is not None:
                entry["target"] = t
        self._pending[str(run_id)] = (spec, args, entry)

    def on_tool_end(self, output, *, run_id=None, **kwargs):
        item = self._pending.pop(str(run_id), None)
        if item is None:
            return
        spec, args, entry = item
        out = _as_dict(output)
        for field in _OUTPUT_FIELDS:
            path = spec.get(field)
            if not path:
                continue
            val = _dig(out, path)
            if val is not None:
                entry[field] = val
        _fill_from_inputs(spec, args, entry)
        if getattr(output, "status", None) == "error":  # ToolMessage(status="error")
            entry["error"] = str(getattr(output, "content", "tool error"))
        elif "status" not in entry and "http_status" not in entry:
            # The tool ran, but returned no provider status: without an id this stays at S2.
            entry["status"] = "done"
        self.calls.append(entry)

    def on_tool_error(self, error, *, run_id=None, **kwargs):
        item = self._pending.pop(str(run_id), None)
        if item is None:
            return
        spec, args, entry = item
        # The call failed, but what was attempted (amount, idempotency key) is still evidence.
        _fill_from_inputs(spec, args, entry)
        entry["error"] = f"{type(error).__name__}: {error}"
        self.calls.append(entry)

    # ------------------------------------------------------------------ helpers
    def write(self, path: str) -> str:
        d = os.path.dirname(path)
        if d:
            os.makedirs(d, exist_ok=True)
        with open(path, "w", encoding="utf-8") as fh:
            json.dump(self.calls, fh, indent=2, default=str)
        return path

    def check(self, reply: str):
        from saydo_diff import diff
        return diff(reply, list(self.calls))

    def reset(self):
        self.calls.clear()
        self._pending.clear()
