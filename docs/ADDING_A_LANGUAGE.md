# Adding a language frontend

A language frontend lowers source into the shared `SemanticIR` boundary. Core
analyzers must remain language-neutral and must not import the new language's
parser.

The executable acceptance criteria are in
[`docs/ARCHITECTURE.md`](ARCHITECTURE.md#acceptance-criteria-for-new-frontends)
and are enforced by [`tests/test_frontend_conformance.py`](../tests/test_frontend_conformance.py).

## Development commands

Run these commands from the repository root:

```bash
python -m pip install -e ".[dev,graph]"
python -m pytest -q tests/test_frontend_conformance.py
ruff check smartbench tests
python -m compileall -q smartbench
```

The conformance suite is offline and deterministic. It runs the same checks
against Python, Go, and JavaScript through one parameterization.

## Add the frontend

1. Implement the adapter under `smartbench/core/adapters/` and the semantic
   lowerer under `smartbench/frontends/`.
2. Expose `language`, `file_extensions`, `semantic_capabilities`,
   `parse_semantic_file`, and `parse_semantic_project` through the shared adapter
   interface.
3. Keep parser-specific details in the frontend. Emit normalized operations,
   control-flow edges, facts, and optional shared `TypeEvidence` assertions.

## Add conformance fixtures

Create `tests/fixtures/frontend_conformance/<language>/` with:

```text
before.<ext>       source containing the known bug
after.<ext>        same source with the bug fixed
malformed.txt      deliberately invalid source, copied to .<ext> by the test
golden_before.json generated SemanticIR snapshot
golden_after.json  generated SemanticIR snapshot
```

The current analyzer fixture contract is language-neutral:

- `before` calls `acquire()` and later `consume()` without a dominating
  `release()` call;
- `after` inserts `release()` before `consume()`.

The suite runs one unchanged `StateInvariant` through `StateMachineAnalyzer`
and requires one violation before the fix and none after it. This proves that
basic rule execution does not require language-specific analyzer changes.

The malformed fixture must produce a visible frontend error. An empty IR with
no error is a failure. If a frontend cannot meet a criterion yet, mark only that
check `xfail` with `strict=True` and link a separate open issue; do not weaken
the assertion or change production code as part of adding the conformance case.

## Register the language

In `tests/test_frontend_conformance.py`:

1. Add a `FrontendCase` for the adapter and fixture directory.
2. Add it to `FRONTEND_CASE_VALUES`, `FRONTEND_CASES`, and `MALFORMED_CASES`.
3. Add a `skipif` marker when the language parser is an optional dependency.
4. If the adapter declares a new capability as `full`, add a behavioral
   assertion for it to `FULL_CAPABILITY_ASSERTIONS`. The suite fails when a
   full capability has no executable assertion.

Partial capabilities must include a non-empty limitation reason and must not
also appear in the full capability set.

## Update golden IR files

After changing a fixture or intentional lowering behavior, regenerate the
snapshots and review the diff:

```bash
python tests/update_frontend_conformance_goldens.py
python -m pytest -q tests/test_frontend_conformance.py
```

Golden serialization normalizes the temporary project path and removes build
timing plus frontend runtime/type-probe metadata. Source bytes, hashes, operation
order, IDs, edges, facts, and type evidence remain part of the snapshot.
