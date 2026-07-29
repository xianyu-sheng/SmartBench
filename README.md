# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Version: 0.7.0](https://img.shields.io/badge/version-0.7.0-4C1.svg)](CHANGELOG.md)
[![Status: Public Beta](https://img.shields.io/badge/status-public_beta-orange.svg)](#project-status)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

SmartBench is an experimental code-diagnosis workbench that combines normalized static analysis with evidence-constrained LLM review.

[中文说明](README_CN.md) · [Live demo](https://xianyu-sheng.github.io/SmartBench/) · [Architecture](docs/ARCHITECTURE.md) · [Usage](docs/USAGE_GUIDE.md)

[![SmartBench terminal demo](docs/assets/smartbench-demo-poster.png)](https://xianyu-sheng.github.io/SmartBench/)

The normal workflow is read-only. SmartBench does not edit the analyzed repository, open an Issue or pull request, or contact a maintainer unless a user performs those actions separately.

> [!IMPORTANT]
> SmartBench is a public Beta, not a production SAST replacement. It can run controlled repository audits and reproduce a small corpus of known defects. It has not established general unknown-bug precision or recall.

## Quick start

Requirements: Python 3.10 or newer and Git.

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[graph]"
```

Run deterministic analysis without an LLM:

```bash
smartbench unified run \
  --project /path/to/repository \
  --output report.json \
  --sarif report.sarif
```

Run the interactive evidence/Agent path:

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project /path/to/repository \
  --concern "find correctness and resource-lifecycle risks" \
  --output agent-report.json
```

The `rag` extra is optional:

```bash
python -m pip install -e ".[graph,rag]"
```

Without it, deterministic graph retrieval remains available but the local vector index is skipped.

## Runtime architecture

The main CLI entry points now share one `AnalysisSession`. Repository discovery, language lowering, SemanticIR construction, and semantic linking run once; deterministic rules, retrieval, ProjectReader, and the debate roles consume the same result.

```text
Repository
  -> ScanPlan -> language frontends -> SemanticIR -> SemanticLinker
  -> AnalysisSession
       |-> deterministic rules and declarative state analyzers
       |-> deterministic GraphRAG
       |-> ProjectReader hypothesis
             -> evidence resolver -> validator -> CFG lifecycle analyzer
       `-> EvidencePack { facts, hypotheses, source references }
             -> Proposer -> source verifier -> Critique -> Judge
  -> JSON / SARIF / benchmark report
```

There are still different consumers, but they no longer rebuild incompatible IRs:

| Command | Uses the shared session | LLM behavior |
| --- | --- | --- |
| `smartbench unified run` | Yes | None by default; deterministic rules only |
| `smartbench quick` / interactive wizard | Yes | Optional ProjectReader plus evidence-constrained review |
| `smartbench benchmark run` | Yes | None; pinned snapshots and declared expectations |
| `smartbench eval-rag` | Yes | None; evaluates retrieval over the session IR |
| `smartbench diagnose` | Not a semantic-analysis entry | Runs local compiler/process/tool probes |

The older `CodeGraph -> SemanticIR.from_graph` wrapper remains for library compatibility and fallback use. The primary CLI no longer uses it as a substitute for full language lowering.

## Evidence boundary

An `EvidencePack` separates two types of input:

- `facts`: source-backed graph or analyzer facts with stable `fact-*` IDs;
- `hypotheses`: ProjectReader interpretations and heuristic-rule candidates with `hypothesis-*` IDs.

Hypotheses are visible to later Agents so they can choose what to investigate, but they are not accepted by the evidence gate as facts. A concrete final suggestion must cite valid fact IDs. Missing, ambiguous, or conflicting evidence remains `unknown` or `abstained`.

ProjectReader does not write to SemanticIR. For the currently implemented resource-lifecycle path it selects a real call and proposes a project-scoped cleanup protocol. The deterministic stages then:

1. bind the selected operation, cleanup facts, and type evidence;
2. reject zero or multiple structural matches;
3. validate result bindings, member paths, reachability, and type selectors;
4. run a language-neutral CFG dominance check;
5. emit a finding or an explicit abstention.

One bounded repair is allowed only after deterministic rejection. It receives the same inventory and rejection reasons, not the analyzer outcome.

## Language support

| Language | Current level | Boundary |
| --- | --- | --- |
| Python | Semantic, deepest | CFG/ICFG, calls, state rules, partial type and data semantics |
| Go | Semantic, deepest | CFG/ICFG, state/resource analysis and surface type evidence; no `go/types` |
| JavaScript / TypeScript | Semantic, partial | Common statements, calls and control flow; async, exceptions and dynamic dispatch remain partial |
| Rust | Structural | Tree-sitter symbols and graph context; no complete semantic lowering |
| Java, Kotlin, C/C++, Ruby, Swift, C#, Zig | Detection / heuristic | Fingerprinting and fallback structure, not compiler-grade analysis |

Language-neutral analyzers do not import a language parser. A new semantic frontend must provide stable locations, explicit capabilities, deterministic IR serialization, and at least one before/after benchmark.

## Reading reports

`unified` reports include findings, capability assessments, source roles, repository zones, SemanticIR statistics, errors, and bounded EvidencePacks. `quick` embeds that deterministic result under `analysis_report` next to the Agent review.

| Status | Meaning |
| --- | --- |
| `full` | The relevant frontend meets every declared requirement for that rule |
| `partial` | A documented approximation ran |
| `unsupported` | Required semantics are unavailable; this is not a clean result |
| `unknown` | The rule did not declare enough semantic requirements to claim coverage |
| `abstained` | Evidence was absent, ambiguous, conflicting, or ownership transfer was possible |

Two additional caveats matter:

- verifier labels such as `verified` and `hallucinated` describe source-location and structural-reference checks; they do not prove that a bug conclusion is correct;
- `consensus_reached` currently means that the Judge returned schema-valid JSON. It is not a statistical agreement score between independent models.

## Reproducible evaluation

The repository contains six public before/after cases (12 snapshots):

| Project | Language | Public fix | Category |
| --- | --- | --- | --- |
| Requests | Python | [GHSA-j8r2-6x86-q33q](https://github.com/advisories/GHSA-j8r2-6x86-q33q) | Security state guard |
| FastAPI | Python | [#5465](https://github.com/fastapi/fastapi/pull/5465) | Resource lifecycle |
| Prometheus | Go | [#1070](https://github.com/prometheus/prometheus/pull/1070) | Resource lifecycle |
| Kubernetes | Go | [#29495](https://github.com/kubernetes/kubernetes/pull/29495) | Resource lifecycle |
| Gin | Go | [#4422](https://github.com/gin-gonic/gin/pull/4422) | Resource lifecycle |
| Terraform | Go | [#38585](https://github.com/hashicorp/terraform/pull/38585) | Resource lifecycle |

```bash
smartbench benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

The current expected result is 12/12 snapshot checks: each declared buggy snapshot produces the expected rule and each fixed snapshot produces none. This shows that SmartBench can express these known defects. It does not measure unknown-bug recall.

The separate blind ProjectReader experiment excludes historical target files from the model inventory. A recorded DeepSeek A/B completed 6/6 trials over two admissible Go protocols after deterministic ID resolution; two other cases remained unsupported because no admissible reference survived exclusion. See [the experiment notes](benchmarks/experiments/project_reader_blind/README.md) for the exact boundary.

## Known limitations

- Most built-in rules are still source heuristics; declarative state rules and the resource-lifecycle analyzer provide the strongest semantic examples.
- Exception flow, async scheduling, dynamic dispatch, alias analysis, goroutine happens-before, and interprocedural channel aliases are incomplete.
- The ProjectReader lifecycle analyzer currently proves normalized defer-style cleanup only.
- The benchmark corpus is small and dominated by resource-lifecycle cases.
- Repository content sent through `quick` is visible to the configured remote model provider.
- A clean report can mean “no supported finding,” not “the repository has no bugs.”

## Safety

- Source paths are confined to the repository root; external symlinks and `../` escapes are ignored.
- External commands run without a shell and have time/output bounds.
- Optional `quick --sandbox` applies proposed patches only to a temporary copy, but repository tests still run with the current user's OS permissions.
- Do not send repositories containing secrets or restricted source code to a remote provider unless that exposure is acceptable.
- Do not file a SmartBench finding upstream without repeated verification and a human decision.

## Documentation and development

- [Architecture](docs/ARCHITECTURE.md)
- [Usage guide](docs/USAGE_GUIDE.md)
- [Demo timeline](docs/DEMO_3_MINUTES_CN.md)
- [Evidence-loop note](docs/EVIDENCE_LOOP_ARTICLE_CN.md)
- [Historical benchmark corpus](benchmarks/real/README.md)
- [Blind ProjectReader experiment](benchmarks/experiments/project_reader_blind/README.md)

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI runs Python 3.10-3.12 tests, parser-adapter checks, the 12-snapshot benchmark, ProjectReader boundary experiments, and a clean-wheel CLI smoke test.

## Project status

SmartBench is suitable for controlled repository experiments, architecture research, and portfolio demonstrations. The next useful work is effect measurement, not more surface features:

1. expand the corpus to 10-20 external before/after cases and natural negatives;
2. add another Agent-derived protocol category and another semantic language case;
3. report precision, recall, abstention rate, trial stability, latency, and provider cost;
4. deepen exception, async, type, alias, and concurrency semantics without weakening the IR boundary.

## License

[MIT](LICENSE)
