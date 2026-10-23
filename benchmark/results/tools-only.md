# Benchmark results (tools-only)

- Bugs caught: **13/15** (recall 87%)
- Precision of confirmed/likely findings: **100%** (0 false alarm(s))
- Clean PRs with a false alarm: **0/6**
- Seeded bugs raised only as 'needs a human check': 0
- Fixes verified in the sandbox: 0
- Model claims discarded by grounding: 0
- Pipeline errors: 0

Recall by how the bug can be detected:

- `lint`: 5/5
- `llm`: 0/2
- `tests`: 8/8

| case | detectable by | result | false alarms | seconds |
|---|---|---|---|---|
| off-by-one-range | tests | caught | - | 1.3 |
| boundary-comparison | tests | caught | - | 1.3 |
| missing-return | tests | caught | - | 1.3 |
| swapped-operands | tests | caught | - | 1.3 |
| empty-average | tests | caught | - | 1.3 |
| inverted-condition | tests | caught | - | 1.3 |
| slice-drops-last | tests | caught | - | 1.3 |
| wrong-dict-key | tests | caught | - | 1.3 |
| undefined-name | lint | caught | - | 1.3 |
| mutable-default | lint | caught | - | 1.3 |
| is-with-literal | lint | caught | - | 0.9 |
| shell-injection | lint | caught | - | 1.0 |
| eval-of-input | lint | caught | - | 1.0 |
| unchecked-discount | llm | missed | - | 0.7 |
| retry-count | llm | missed | - | 0.7 |
| clean-rename | none | clean (no alarm) | - | 1.0 |
| clean-docstring | none | clean (no alarm) | - | 1.0 |
| clean-new-function | none | clean (no alarm) | - | 1.0 |
| clean-simplify | none | clean (no alarm) | - | 1.0 |
| clean-guard | none | clean (no alarm) | - | 1.0 |
| clean-fstring | none | clean (no alarm) | - | 1.0 |
