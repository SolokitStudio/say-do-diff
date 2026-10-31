# planlock sample run

A real [planlock](https://github.com/JosephCurwin/planlock) v0.7.0 session (commit `e7be274`) on its
`micropayout` profile, recorded on 2026-10-30. The files are planlock's own output, unedited:

- `session_events.log`: `$LOG_ROOT/session_events.log`, one JSON row per record or lifecycle event.
- `vm_io/outbox/<txn_id>.job.json`: the job packets planlock's env-inject adapter wrote for each
  `PATCH /feature/implement/active` step, with the ordered tool arguments (`job.ops`).
- `profile.micropayout.json`: the domain profile the run used.

What happened: an investigation call (`checkCompliance`, plus a denied `submitToNetwork`), then an
approved three-step plan. In step 1 (reserve) and step 2 (pay), an out-of-bounds call was blocked
first, then a bound retry ran, and both steps' audits passed. Step 3 (ledger) was blocked by a pin
(`getAccountBalance.currency` had to be `USD` but was `EUR`), so the plan went on hold.

`reply.txt` claims all three steps. `tool_map.json` tells Say-Do Diff that `submitToNetwork` is a
payment and `reserveFunds` a booking.

```bash
python3 -m saydo_diff -m examples/planlock/reply.txt -l examples/planlock/session_events.log \
  --from planlock --map examples/planlock/tool_map.json --plan
```

The result is 2 gaps (the ledger claim and "All set."), exit code 1. The planlock log stores
`outbox_dir` as an absolute path from the recording machine; when that path is missing, Say-Do Diff
looks in `vm_io/outbox` next to the log, so this folder works as is.
