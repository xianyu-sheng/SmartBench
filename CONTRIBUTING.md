# Contributing to SmartBench

Thanks for your interest. SmartBench is a public beta exploring a specific
division of responsibility in code diagnosis: deterministic analyzers own source
facts, an LLM may propose hypotheses, and resolvers decide whether a hypothesis
can be bound back to real source operations.

That boundary is the point of the project. Contributions that blur it — for
example, promoting a model's assertion directly into a finding — will be asked to
change, even if the result looks better on a benchmark.

## Development setup

Requires Python 3.10+ and Git.

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[dev,graph]"
```

The `rag` extra is optional; without it the local vector index is skipped but
deterministic graph retrieval still works.

## Before opening a pull request

```bash
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
```

All three must pass. CI additionally runs Python 3.10-3.12, parser-adapter
checks, the 12-snapshot benchmark, ProjectReader boundary experiments, and a
clean-wheel CLI smoke test.

## What makes a change easy to accept

**Keep the evidence boundary intact.** `facts` are source-backed with `fact-*`
IDs. `hypotheses` are untrusted with `hypothesis-*` IDs. Nothing should move a
hypothesis into the fact namespace, and the evidence gate must never accept a
hypothesis ID.

**Prefer explicit uncertainty over a confident guess.** `unknown`, `partial`,
`unsupported`, and `abstained` are useful results. A rule that reports nothing
because it could not analyze the code must not look like a rule that reported
nothing because the code is clean.

**Add a test that would fail without your change.** For bug fixes, a regression
test. For new analysis, at least one before/after fixture.

**Match the existing style.** Read the surrounding code first. Line length is
100; ruff enforces `E`, `F`, `I`, `N`, `W`.

## Adding a diagnostic rule

Rules live in `smartbench/core/rules/`. Declare the semantic capabilities your
rule requires — if you do not, the engine reports its status as `unknown` and
cannot claim coverage. Do not import a language parser from a rule; rules are
language-neutral by design.

Consider whether your rule can be expressed as a declarative state-machine
invariant (`smartbench.state-rules/v1` YAML) instead of Python. See
`benchmarks/reasonix/reasoning_stop.yaml` for a worked example.

## Adding a language frontend

See the acceptance criteria in [docs/ARCHITECTURE.md](docs/ARCHITECTURE.md).
A frontend must provide deterministic source-location round trips, an explicit
capability declaration, stable IR serialization, at least one pre-fix/post-fix
benchmark, and must not require changes to language-neutral analyzers.

Declaring a capability you have not proven is worse than declaring `partial`.

## Adding a benchmark case

Cases live in `benchmarks/real/` as minimal before/after source snapshots derived
from a public fix, with a `manifest.yaml` and `rules.yaml`. Snapshots preserve
only the code the declared analyzer needs; they are not full repository
checkouts. Link the upstream fix in the case README.

## Reporting bugs

Include the SmartBench version, Python version, which extras are installed, the
exact command, and what you expected versus what happened. A minimal fixture that
reproduces the problem is the single most useful thing you can provide.

If the report involves a repository you cannot share, a reduced synthetic file
that triggers the same behaviour works just as well.

## Security

Do not open a public issue for a security problem. See
[SECURITY.md](SECURITY.md).

## Scope notes

SmartBench is not a production SAST replacement, and a clean report means "no
supported finding," not "no bugs." Contributions that oversell what the tool
establishes — in code, output text, or documentation — are the one category most
likely to be rejected outright.
