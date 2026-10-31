# Microsoft Agent Governance Toolkit samples

Audit logs written by the [Agent Governance Toolkit](https://github.com/microsoft/agent-governance-toolkit)
itself, not hand-made:

| File | Written by | Shape |
|---|---|---|
| `maf_audit.jsonl` | `agent-governance-toolkit-core` 5.0.0 `AuditLog` + `FileAuditSink` | signed JSONL (`content_hash`, `previous_hash`, HMAC `signature`) |
| `maf_export.json` | the same `AuditLog.export()` | `{exported_at, merkle_root, chain_root, entry_count, entries}` |
| `maf_cloudevents.json` | the same `AuditLog.export_cloudevents()` | CloudEvents 1.0, `ai.agentmesh.*` |
| `proxy_audit.jsonl` | `FileAuditSink`, rows as the agentmesh MCP proxy logs them (`mcp_tool_call`) | signed JSONL |
| `ts_audit.json` | `@microsoft/agent-governance-sdk` 5.0.0 `AuditLogger.exportJSON()` | `{timestamp, agentId, action, decision, hash, previousHash}` |

`make_fixtures.py` and `make_ts_fixture.cjs` regenerate them. Each `audit.log()` call mirrors what
the Agent Framework (MAF) middleware and the MCP proxy write. The HMAC key is the fixture-only
`saydo-diff-fixture-secret`:

```bash
AGT_AUDIT_SECRET=saydo-diff-fixture-secret \
  python3 -m saydo_diff -m examples/agt/reply.txt -l examples/agt/maf_audit.jsonl --from agt
python3 -m saydo_diff -m examples/agt/proxy_reply.txt -l examples/agt/proxy_audit.jsonl --from auto
```

The MAF run looks up order 7731 and refunds the duplicate charge (`re_3QF8x2LkT`). Its `send_email`
tool raises, so only a `start` entry is logged. The capability guard blocks `delete_customer_account`,
and a Slack post succeeds. `reply.txt` claims all four, so the report shows 3 gaps in 5 claims and
exits 2 (stop-ship: the delete claim). The proxy log records only decisions, so the email claim stays
at S1.

`tests/agt_crosscheck.py` checks these files with AGT's own `verify_integrity()` and
`AuditEntry.verify_hash()`, and confirms freshly generated logs grade the same.

Format notes from AGT 5.0.0:
- `export()` puts the entries at the top level, not under the `metadata` key the tutorial shows.
- Export timestamps are `str(datetime)`, with a space before the time.
- CloudEvents `time` comes out as `...+00:00Z`, which isn't strict RFC 3339.
