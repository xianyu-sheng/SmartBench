# Negative corpus (false-positive gate)

Ordinary clean code that **superficially resembles** a rule's bug pattern. The
suite asserts **zero findings at the default confidence threshold (0.7)** on
every fixture; any finding is a false positive and fails CI.

This is the corpus requested in [#11](https://github.com/xianyu-sheng/SmartBench/issues/11).
It complements the before/after corpus in `benchmarks/real/`: those snapshots
prove that an analyzer distinguishes a fix from its own bug, while these
fixtures sample the space of *ordinary clean code* around the same patterns.

## What is in it

| Category | Fixtures | Rule under test |
|---|---|---|
| Resource lifecycle | context manager, `try/finally`, `defer`, ownership transfer, cleanup wrapper | `resource_leak` |
| Secrets | example placeholder, test fixture, documented deployment placeholder | `hardcoded_secret` |
| Command construction | constant command, list arguments, validated integer | `command_injection` |
| State guards | early guard clause, helper guard, Go nil guard | `null_dereference` |

Each fixture starts with a header comment that states:

1. **Resembles** — the bug pattern a naive detector might match;
2. **Not a bug because** — the control-flow or ownership fact that makes it clean;
3. **True positive if** — the smallest change that would turn it into a real bug.

## Sampling rules (how to add a fixture)

- **Surface resemblance, not trivia.** Use the same API or statement shape as
  the bug; keep the code otherwise ordinary. A one-liner that cannot trigger the
  rule is not a negative sample.
- **Cover the near-miss mechanisms**, not just the happy path: context manager,
  `try/finally`, wrapper function, ownership transfer, guarded helper, constant
  or validated input, documented placeholder.
- **Write real-looking code**: imports, function context, error handling.
  Avoid `TODO`/`FIXME`, f-strings, unsafe randomness or other patterns that
  trigger unrelated rules.
- **Keep the threshold honest.** Snapshots run with `min_confidence: 0.7` — the
  CLI default. A finding below that threshold is not user-visible by default,
  but a rule that emits it on clean code still deserves a fix; use the
  fixture to decide which one you are testing.
- **Pair with a positive.** For each new category, make sure a positive case
  exists in `benchmarks/real/` so precision and recall are measured together.
- **One bug shape per fixture.** If a fixture combines two patterns, a failure
  does not tell you which rule misfired.

## Layout

```
benchmarks/negative/
  manifest.yaml          # same schema as benchmarks/real/manifest.yaml
  README.md              # this file
  resource_lifecycle/<fixture>/...
  secrets/<fixture>/...
  command_injection/<fixture>/...
  state_guard/<fixture>/...
```

The manifest uses the benchmark schema (`smartbench.benchmarks/v1`). Snapshots
declare `max_findings: 0` and `min_confidence: 0.7`; the runner treats a
violation as a failure, exactly like a positive case that misses its expected
rule.

## Running

```bash
smartbench benchmark run \
  --manifest benchmarks/negative/manifest.yaml \
  --output negative-report.json
```

CI runs the same command in the `benchmark` job. Locally, the test suite also
runs the corpus (`tests/test_negative_corpus.py`) so a regression fails fast.

## Notes

- `resource_leak` is a heuristic line scanner; the deterministic
  `project_resource_lifecycle` analyzer remains the authoritative check for
  defer-style cleanup. The fixtures for `try/finally`, `defer` and ownership
  transfer exist because the heuristic used to flag them.
- Placeholder and test-fixture secrets are deliberately down-weighted by
  `hardcoded_secret` instead of suppressed, so they are invisible at the
  default threshold but still visible with `--min-confidence 0.3`.
