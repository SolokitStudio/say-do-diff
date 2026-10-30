# Contributing

Thanks for helping. Say-Do Diff is small on purpose, so changes are easiest to review when they are
small too.

## Ground rules

- **Standard library only** in `saydo_diff/`. Examples may use a framework, but their tests must run
  without API keys or network access (skip cleanly when the framework is not installed).
- **Deterministic.** No model calls, no randomness, no network in the checker.
- **Honest grading.** When in doubt, a change should flag rather than pass. A clean report must keep
  meaning "nothing in the built-in lexicon was unsupported", not "the reply is true".
- One topic per pull request, with a test that fails before the change and passes after.

## Setup

```bash
git clone https://github.com/SolokitStudio/say-do-diff.git && cd say-do-diff
python3 -m unittest discover -s tests          # Python 3.10+
python3 -m saydo_diff --message examples/reply.txt --log examples/tool_log.json
```

Optional: `pip install langchain-core` to run the LangChain integration test.

## Good places to start

- Issues labelled [`good first issue`](https://github.com/SolokitStudio/say-do-diff/labels/good%20first%20issue).
- **New verb or umbrella phrase**: add it to `VERBS` or `UMBRELLA` in `saydo_diff/core.py`, plus one
  test with a sentence that should be caught and one that should not.
- **New framework adapter**: copy the layout of `examples/n8n` or `examples/langchain`: one
  converter file, a README with run steps and limits, a sample input, and tests in `tests/`.

## Style

- Keep functions short and typed where it helps; follow the existing code style (PEP 8, 110 columns).
- README claims must match what the code does. If you add a limit, document it under **Limits**.

## Reporting bugs

Open an issue with the reply text, the tool log (redact anything private), the output you got and
the output you expected. A false negative (a claim that should have been flagged) is the most useful
bug report this project can get.

## License

By contributing you agree that your contribution is licensed under the MIT License.
