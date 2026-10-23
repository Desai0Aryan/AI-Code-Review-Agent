# Benchmark

Corpus: `benchmark/corpus.py`. Each case is a tiny correct project with passing tests plus one PR that
introduces a single bug or a harmless change. `how` records what can detect it: `tests`, `lint`, `llm` (no covering test), `none` (clean).

Scoring (`benchmark/runner.py`): a bug is *caught* when a confirmed/likely finding sits in the function where
it was seeded, or a related test that passes on base fails with the PR. Every other confirmed/likely finding is a
*false alarm*. Findings only raised as "needs a human check" are reported separately and do not count as caught.

Run: `python -m benchmark` (tools only) and `python -m benchmark --llm` (needs `CRA_ANTHROPIC_API_KEY`;
set `CRA_LLM_TEMPERATURE=0` for repeatability). Output: `benchmark/results/*.md|json`.

## Deterministic baseline (committed in `benchmark/results/tools-only.md`)

13/15 bugs caught, 0 false alarms, 0/6 clean PRs flagged. Misses are the 2 `llm`-only cases.
The LLM run has not been executed in the environment this was built in (no API key); run it to fill in your own numbers.

## Caveats

The corpus is small and synthetic, written by the author of the tool; treat the numbers as a regression
check, not a claim about real-world accuracy.
