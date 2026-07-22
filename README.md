# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)
[![Status: Beta](https://img.shields.io/badge/status-beta-orange.svg)](#project-status)

Evidence-grounded code diagnosis for local repositories.

[中文说明](README_CN.md) · [Usage guide](docs/USAGE_GUIDE.md)

SmartBench combines deterministic repository fingerprinting, structural code parsing, optional RAG retrieval, a three-role LLM review, local diagnostic probes, and on-disk evidence checks. It does not edit source files in the target repository; optional RAG indexing writes cache data under `<project>/.smartbench/`.

> SmartBench is beta software. Evidence verification confirms cited files, lines, symbols, and selected call relationships. It reduces invented references, but it cannot prove that every diagnosis is semantically correct.

## What works today

- Deterministic language, framework, build-system, entry-point, dependency, and Git-signal detection.
- Tree-sitter symbol extraction for Python, Go, JavaScript, TypeScript, and Rust when the `graph` extra is installed.
- Regex fallback for broader language coverage and approximate call relationships.
- Three review roles: Proposer, Critique, and Judge. They can share one model or use separate models.
- Deterministic checks for cited paths, line ranges, symbols, and selected call chains; fuzzy path corrections are marked partial.
- Optional graph plus local-vector hybrid retrieval, with a labeled retrieval-evaluation fixture.
- Strategy-based local diagnostic probes and recommendations for Python, Go, C/C++, Java/Kotlin, and system signals.
- Mixed-language repositories route local diagnostics across every detected language instead of collapsing to an empty generic result.
- JSON report output for non-interactive workflows.

## How it works

```text
repository
   │
   ├─ deterministic fingerprint ── language / framework / build / Git signals
   │
   ├─ structure graph ──────────── tree-sitter symbols + heuristic fallback
   │                │
   │                └─ optional local RAG index
   │
   ├─ selected local diagnostic probes
   │
   └─ Proposer → evidence check → Critique → evidence check → Judge
                                               │
                                               └─ scored findings with locations
```

A finding with a missing file or invalid line is marked hallucinated. A fuzzy path correction is marked partial rather than silently accepted. Semantic correctness still requires tests, compiler or linter output, profiling data, or human review.

## Language coverage

Repository fingerprinting recognizes Python, Go, Rust, C, C++, Java, Kotlin, JavaScript, TypeScript, Ruby, Swift, C#, and Zig, plus mixed-language repositories.

The optional tree-sitter backend currently covers Python, Go, JavaScript, TypeScript, and Rust. Other languages use heuristic structure extraction; C currently receives file-level discovery. The fallback graph is useful for retrieval context but is not compiler-grade analysis.

## Quick start

Requirements: Python 3.10 or newer and Git.

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e .
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
- Host process, memory, and kernel probes (`ps`, `vmstat`, and `dmesg`) are disabled by default. `diagnose --system-probes` explicitly enables them and their output may expose host information.
- `--sandbox` is explicit opt-in. It protects the working tree, but it is not an OS security boundary: repository tests still run with your user permissions.
- Evidence status describes whether a reference is grounded, not whether a vulnerability or fix has been formally proven.

## Project layout

```text
smartbench/
├── cli/             command definitions, wizard, phases, and display
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

CI runs lint, compilation, and tests on Python 3.10, 3.11, and 3.12; separately exercises all five parser adapters and builds both a wheel and source distribution.

## Project status

SmartBench is a diagnostic workbench, not a replacement for compilers, linters, security scanners, profilers, or human review. The next quality milestones are:

1. Publish measured retrieval and diagnostic precision against labeled cases.
2. Export a standard machine-readable review format such as SARIF.
3. Add stronger process isolation for optional repository test execution.
4. Expand machine-applicable patch coverage and language-specific validation.

## License

[MIT](LICENSE)
