# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Version: 0.7.0](https://img.shields.io/badge/version-0.7.0-4C1.svg)](CHANGELOG.md)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#project-status)

Language-agnostic, evidence-grounded code diagnosis for local repositories.

[中文说明](README_CN.md) · [Usage guide](docs/USAGE_GUIDE.md)

SmartBench is an iterative diagnostic workbench rather than a claim of “perfect
automatic bug detection”. It combines a language-neutral Semantic IR,
deterministic graph evidence retrieval, declarative control/data/state analyses,
optional local RAG, a three-role LLM review, local diagnostic probes, and
source-backed evidence checks. The normal pipeline does not edit source files
in the target repository; optional RAG indexing writes cache data under
`<project>/.smartbench/`.

> SmartBench is beta software. Evidence verification confirms cited files, lines, symbols, and selected call relationships. It reduces invented references, but it cannot prove that every diagnosis is semantically correct.

## What works today

- Deterministic language, framework, build-system, entry-point, dependency, and Git-signal detection with dependency pruning and explicit scan limits.
- Tree-sitter symbol extraction for Python, Go, JavaScript, TypeScript, and Rust when the `graph` extra is installed.
- Conservative operation-level Python/Go call linking with explicit resolved,
  unresolved, and ambiguous counts; Go spawn/defer and intraprocedural channel
  synchronization use the same semantic edge model.
- A shared JavaScript/TypeScript frontend lowers functions, parameters,
  assignments, branches, loops, returns, calls, and surface type annotations
  into the same operation model; its async/exception/type semantics remain
  explicitly partial.
- A versioned `SemanticIR` boundary with frontend contracts, capability
  declarations, normalized operations, control-flow edges, provenance-bearing
  language-neutral type evidence, and explicit
  `full`/`partial`/`unsupported`/`unknown` analysis status in every JSON
  report. `unknown` means a rule has not declared a semantic capability
  contract; it is never promoted to `full`.
- Deterministic source provenance (`production`, `test`, `fixture`, `example`,
  `generated`, and `documentation`) shared by all rules, plus an independent
  repository zone (`first_party`, `legacy`, `third_party`, `vendored`, or
  `generated`); production-scoped rules do not promote fixture findings to
  product-level bug claims.
- Bounded interprocedural control-flow and data-flow for Python/Go, including
  call/return paths, argument/parameter links, return propagation, and
  declarative cross-function state rules.
- Conservative Go surface-type evidence for parameters, method receivers,
  struct fields, local assignment propagation, and local results. Portable
  analyzers require exact normalized type identity and abstain on missing or
  conflicting evidence; the capability remains partial because this is not
  `go/types`.
- Deterministic EvidencePacks with stable fact IDs, graph snapshot hashes, and
  source locations. Evidence-exclusive debate mode rejects suggestions that do
  not cite facts from the pack.
- A deterministic ProjectReader evidence resolver binds Agent semantic
  selectors to unique call, cleanup, and type evidence. Missing or ambiguous
  matches abstain; model-copied opaque IDs cannot enter the validator.
- Regex fallback for broader language coverage and conservative approximate call relationships; ambiguous duplicate definitions do not receive invented edges.
- Three review roles: Proposer, Critique, and Judge. They can share one model or use separate models.
- Deterministic checks for cited paths, line ranges, symbols, and selected call chains; fuzzy path corrections are marked partial.
- Optional graph plus local-vector hybrid retrieval, with a labeled retrieval-evaluation fixture.
- Strategy-based local diagnostic probes and recommendations for Python, Go, C/C++, Java/Kotlin, and system signals.
- Mixed-language repositories route local diagnostics across every detected language instead of collapsing to an empty generic result.
- Git URLs, worktrees, and nested monorepo projects are recognized; external command output and execution time are bounded.
- JSON report output for non-interactive workflows.

The semantic path is intentionally conservative: an unresolved call, dynamic
dispatch, unsupported concurrency relation, or unproven guard is reported as
unknown or partial rather than silently treated as a clean result.

## How it works

```text
source repository
   │
   ├─ deterministic fingerprint ── language / framework / build / Git signals
   │
   ├─ language frontend ────────── Python/Go + partial JS/TS SemanticIR
   │                │
   │                ├─ CFG / ICFG / data-flow / state rules
   │                └─ deterministic GraphRAG EvidencePack
   │                                   │
   └─ Proposer → Critique → Judge ────┘
                         │
                         └─ source-backed findings and suggestions
```

A finding with a missing file or invalid line is marked hallucinated. A fuzzy path correction is marked partial rather than silently accepted. Semantic correctness still requires tests, compiler or linter output, profiling data, or human review.

## Language coverage

Repository fingerprinting recognizes Python, Go, Rust, C, C++, Java, Kotlin, JavaScript, TypeScript, Ruby, Swift, C#, and Zig, plus mixed-language repositories.

The optional tree-sitter backend currently covers Python, Go, JavaScript,
TypeScript, and Rust. Python and Go lower to the common semantic operation
model with the deepest current control-flow support. JavaScript and TypeScript
now lower the common statement/call/type surface through one shared frontend,
while async scheduling, exceptions, dynamic dispatch, and type-checker facts
remain partial. Java and Rust currently provide structural compatibility.
Other languages use heuristic structure extraction; C currently receives
file-level discovery. The fallback graph is useful for retrieval context but
is not compiler-grade analysis.

## Quick start

Requirements: Python 3.10 or newer and Git.

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
smartbench --version
smartbench --help
```

Install precise parsers for the five tree-sitter languages:

```bash
python -m pip install -e ".[graph]"
```

Start the interactive wizard:

```bash
smartbench
```

Run a non-interactive review using environment credentials:

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project . \
  --concern "find correctness and concurrency risks" \
  --output report.json
```

## Unified Multi-Language Diagnosis

SmartBench provides a fast, unified diagnostic framework with multi-language support:

```bash
# List available diagnostic rules
smartbench unified rules

# List supported languages
smartbench unified languages

# Run unified diagnosis
smartbench unified run --project .

# Run with specific rules and languages
smartbench unified run --project . --rule null_dereference --rule hardcoded_secret --language python

# Export SARIF report (standard format for GitHub/GitLab code scanning)
smartbench unified run --project . --sarif report.sarif --output report.json

# Run a versioned declarative state-machine rule
smartbench unified run \
  --project /path/to/repository \
  --language go \
  --state-rules benchmarks/reasonix/reasoning_stop.yaml \
  --output state-report.json

# Run a pre-fix/post-fix benchmark manifest
smartbench benchmark run \
  --manifest benchmarks/your-project/manifest.yaml \
  --output benchmark-report.json

# Run the auditable historical FastAPI before/after case
smartbench benchmark run \
  --manifest benchmarks/real/fastapi_form_cleanup/manifest.yaml \
  --output fastapi-benchmark-report.json

# Run the offline three-minute gate -> repair -> CFG witness demo
python -m smartbench.experiments.evidence_loop_demo \
  --output evidence-loop-demo.json
```

Portfolio walkthroughs: [three-minute demo](docs/DEMO_3_MINUTES_CN.md) and
[evidence-loop architecture article](docs/EVIDENCE_LOOP_ARTICLE_CN.md).

Python and Go semantic frontends normalize control-flow operations into the
same IR. Declarative state rules use the versioned
`smartbench.state-rules/v1` schema, remain outside language frontends, and
produce source-backed JSON/SARIF findings. In evidence-exclusive multi-agent
mode, every accepted suggestion must cite a stable `fact-*` ID from the
deterministic EvidencePack. Benchmark manifests declare snapshot paths and
finding expectations, so regression claims are reproducible and measurable.

The JSON result also exposes `analysis_status` and `stats.rules_*`. A `partial`
rule result is an honest bounded approximation (for example, intra-procedural
taint analysis); `unsupported` means the rule was not run and is never treated
as a clean result; `unknown` means the rule has no declared semantic
requirements. Each finding carries its source role, repository zone, and
analysis method (`heuristic` or `semantic`) when the frontend can resolve it.

Try the included cross-function benchmark:

```bash
python -m smartbench.cli.main benchmark run \
  --manifest benchmarks/interprocedural/manifest.yaml
```

The benchmark intentionally contains a caller-to-callee event/action path:
the `before` snapshot reports one finding and the `after` snapshot reports
none. It is an architectural regression fixture, not a claim of broad
diagnostic accuracy.

Inspect available local probes, or run the diagnosis-only path:

```bash
smartbench check
smartbench diagnose --project . --perf --output diagnostics.json
smartbench diagnose --project . --perf --system-probes
smartbench eval-rag --project . --queries tests/fixtures/rag_eval_queries.json
```

For a trusted repository, `smartbench quick --project . --sandbox` asks the
Judge for unified diffs, applies only valid patches to a temporary copy, checks
that baseline tests pass, and then runs the same tests after the patch. A
natural-language suggestion without a patch is reported as skipped, never as
verified.

Credential variables are `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GLM_API_KEY`, `DOUBAO_API_KEY`, `MOONSHOT_API_KEY`, and `DASHSCOPE_API_KEY`. Anthropic uses its native Messages protocol; the others use their OpenAI-compatible chat endpoints. Override a quick-mode default with `SMARTBENCH_<PROVIDER>_MODEL`, for example `SMARTBENCH_ANTHROPIC_MODEL`. The wizard keeps entered keys in process memory.

Each debate role is accepted only after its JSON object matches the required schema; malformed or wrong-shaped responses are retried and an invalid Judge response is never reported as consensus. `--output` is written atomically, and a write failure returns a non-zero CLI status.

## Optional RAG

```bash
python -m pip install -e ".[rag]"
```

SmartBench uses graph-only retrieval when the optional RAG stack is unavailable. When enabled, its local vector store writes an index under the analyzed repository's `.smartbench/` directory; add that directory to the target repository's ignore rules if needed. Indexing prunes dependency/cache directories and defaults to at most 2,000 files, 2 MB per file, and 10,000 chunks.

## Safety boundary

- Source files in the analyzed repository are not edited by the normal diagnosis pipeline.
- Git URLs are cloned non-interactively into a temporary directory that is removed when SmartBench exits.
- Scanning, retrieval, and evidence verification read only regular files resolved inside the project root; external symlinks and `../` escapes are ignored.
- Repository metadata, README text, source, logs, tool output, and prior model output are marked as untrusted data in prompts; embedded instruction-like text is not intended to control the workflow. This mitigates prompt injection but is not a formal isolation boundary, so do not analyze repositories containing secrets you cannot expose to the configured model provider.
- Git remote credentials plus URL query/fragment data are removed before the remote is stored in the fingerprint or displayed.
- Project-scoped diagnostics can execute installed compilers or analyzers inside the target path. Use SmartBench only on repositories you trust.
- External tools are run without a shell, with time and output bounds; timeout termination covers the spawned process group on POSIX systems.
- Host process, memory, and kernel probes (`ps`, `vmstat`, and `dmesg`) are disabled by default. `diagnose --system-probes` explicitly enables them and their output may expose host information.
- `--sandbox` is explicit opt-in. It protects the working tree, restricts patches to the declared target file, and removes credential-like environment variables, but it is not an OS security boundary: repository tests still run with your user permissions and may access the network or user files.
- Evidence status describes whether a reference is grounded, not whether a vulnerability or fix has been formally proven.

## Project layout

```text
smartbench/
├── cli/             command definitions, wizard, phases, and display
├── core/             unified engine, rules, adapters, and SARIF bridge
├── ir/               versioned SemanticIR, contracts, facts, and capabilities
├── analysis/         CFG, ICFG, interprocedural, and state analysis
├── frontends/        Python/Go and shared JavaScript/TypeScript lowering
├── detector/        deterministic repository fingerprint
├── graph/           tree-sitter adapters, fallback graph, graph retrieval
├── rag/             optional chunking, embeddings, vector retrieval, evaluation
├── engine/          Proposer / Critique / Judge orchestration
├── verifier/        file, line, symbol, and call-chain evidence checks
├── diagnostics/     local diagnostic tool registry and strategy executor
├── llm/             provider configuration and model calls
└── prompts/         context-aware structured prompts
```

The former Raft-specific implementation remains under `legacy/` for historical reference and is excluded from package builds.

## Development

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI runs lint, compilation, and tests on Python 3.10, 3.11, and 3.12; separately exercises all five parser adapters, builds both a wheel and source distribution, installs the wheel in a clean environment, and smoke-tests the installed CLI outside the checkout.

## Validation snapshot

The current development snapshot was validated on 2026-07-24 with:

- 550 tests passing with the graph extras installed; 512 passing and 38 skipped
  without optional parsers, including the semantic frontends,
  interprocedural linker/ICFG, evidence gate, and benchmark tests.
- Ruff, bytecode compilation, wheel, and source-distribution checks passing.
- The included interprocedural benchmark passing with before=1 and after=0.
- The auditable FastAPI FormData lifecycle benchmark passing with before=1 and
  after=0, using the historical commits recorded in its manifest.
- The Reasonix reasoning-stop benchmark detecting the known pre-fix issue and
  producing no finding for the fixed snapshot.

The benchmark evidence is intentionally small. It demonstrates that the
pipeline can detect a real defect and a cross-function regression, but it does
not yet establish general precision or recall across languages and bug types.

Historical benchmark cases retain their source repository, fixing PR,
before/after commits, bug category, and expected behavior in the machine-readable
report. The FastAPI case is documented in
[`benchmarks/real/fastapi_form_cleanup/README.md`](benchmarks/real/fastapi_form_cleanup/README.md).

Release details are recorded in the [changelog](CHANGELOG.md).

## Project status

SmartBench is a diagnostic workbench, not a replacement for compilers, linters,
security scanners, profilers, or human review. It is usable today for
evidence-grounded repository review and is designed to improve incrementally.
The next quality milestones are:

1. Freeze and extend the SemanticIR contracts and frontend conformance suite.
2. Expand retrieval and diagnostic precision measurement to independent labeled
   repositories and negative cases.
3. Add stronger process isolation for optional repository test execution.
4. Deepen Go/Python type, exception, async, and concurrency semantics.
5. Add another full semantic frontend, then expand machine-applicable patch
   coverage and language-specific validation.

## License

[MIT](LICENSE)
