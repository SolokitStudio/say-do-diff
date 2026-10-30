# Roadmap

Say-Do Diff stays small: a read-only checker, standard library only, no model in the loop.
This list is a plan, not a promise of dates. Ideas and pull requests are welcome in
[Discussions](https://github.com/SolokitStudio/say-do-diff/discussions) and issues.

## Shipped

- **v0.1.0**: CLI and Python API; evidence grades S0–S4 and F; unsupported claims, wrong targets,
  partial batches, money mismatches, umbrella claims, duplicate calls; JSON report; exit codes.
- **n8n example** (`examples/n8n`): turn a saved n8n execution into a tool log.
- **v0.2.0**: GitHub Action (`uses: SolokitStudio/say-do-diff@v0.2.0`) that checks one run or a
  folder of saved runs and fails CI on unsupported claims; LangChain / LangGraph callback handler
  (`examples/langchain`).
- **v0.2.1**: LangChain callback keeps attempted arguments on failed tool calls; Python 3.10+;
  CI; moving `v0` tag (`uses: SolokitStudio/say-do-diff@v0`).

## Next

- **More framework adapters**, each a small example plus tests that need no API key:
  - OpenAI Agents SDK (tool call / tool output items from a run)
  - CrewAI task and tool events
  - LlamaIndex callback / instrumentation events
  - Vercel AI SDK `steps` (`toolCalls` / `toolResults`) via a small JSON converter
- **Log converters** for common trace formats (OpenTelemetry GenAI spans, Langfuse traces).
- **Lexicon coverage**: more completed-action verbs and umbrella phrases, with a test per phrase.
- **`--explain`** output that lists which log entries were matched to each claim.
- **Action niceties**: a Markdown table of gaps per run in the job summary; a `paths` glob input.
- **PyPI release** of the `saydo-diff` package.

## Not planned

- Calling a model to judge replies. The check stays deterministic and offline.
- Rewriting replies or blocking sends. This repo reports; enforcement is out of scope.
