# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Version: 0.7.0](https://img.shields.io/badge/version-0.7.0-4C1.svg)](CHANGELOG.md)
[![Status: Public Beta](https://img.shields.io/badge/status-public_beta-orange.svg)](#project-status)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

**Evidence-grounded code diagnosis where LLMs propose hypotheses and deterministic analysis decides what is supportable.**

[中文说明](README_CN.md) · [▶ Watch the 2m39s live demo](https://xianyu-sheng.github.io/SmartBench/) · [Demo timeline](docs/DEMO_3_MINUTES_CN.md) · [Architecture article](docs/EVIDENCE_LOOP_ARTICLE_CN.md) · [Usage guide](docs/USAGE_GUIDE.md)

[![Watch SmartBench analyze a real Requests repository](docs/assets/smartbench-demo-poster.png)](https://xianyu-sheng.github.io/SmartBench/)

**[▶ Open the browser player](https://xianyu-sheng.github.io/SmartBench/)** · [Download the H.264 MP4](https://raw.githubusercontent.com/xianyu-sheng/SmartBench/main/docs/assets/smartbench-demo.mp4)

*This 2m39s silent Ubuntu GNOME recording starts from a new terminal, runs `smartbench`, imports the complete Requests repository at its public pre-fix commit, builds the graph/RAG index, and shows Proposer → Verifier → Critique → Judge. It is a live run, not a replayed report. No API key or audio track appears in the video.*

SmartBench is a language-neutral diagnostic workbench for local repositories. It combines SemanticIR, CFG/ICFG and state analysis, deterministic graph retrieval, and evidence-constrained Agents. The normal diagnosis path is read-only: it does not patch the repository or contact an upstream project.

> [!IMPORTANT]
> SmartBench is a public Beta, not a production SAST replacement. A grounded reference proves where evidence came from; it does not automatically prove that every diagnosis is semantically correct. Unsupported semantics remain `unknown`, `partial`, or `abstain` instead of being reported as clean.

## Why SmartBench is different

Most LLM code-review tools ask the model to both interpret a project and judge its own evidence. SmartBench separates those responsibilities:

```text
Repository
  → language frontend → SemanticIR
  → deterministic inventory / GraphRAG EvidencePack
  → Agent semantic hypothesis
  → unique-match evidence resolver
  → unchanged validator
  → CFG / ICFG / state analyzer
  → Finding, or explicit abstention
```

The Agent may select an operation, result position, cleanup method, member path, or type hypothesis. It cannot create SemanticIR facts. Opaque `fact-*` and `type-*` IDs are bound by the deterministic resolver:

- exactly one structural match → `resolved`;
- no match → `unresolved` and abstain;
- multiple matches → `ambiguous` and abstain;
- older Agent-supplied IDs remain visible for audit but cannot override resolved evidence.

If a semantic selector is rejected, bounded repair receives only the same blind inventory, the previous structured output, and deterministic rejection reasons. It never receives the historical target snapshot or the analyzer's before/after answer, and the replacement model must pass through the same resolver and validator.

## Proof, not a pitch

Current reproducible snapshot, verified on **2026-07-29**:

| Evidence | Result | What it establishes |
| --- | ---: | --- |
| Test suite with graph extras | **586 passed** | Contracts, frontends, resolver, analyzers, reports, and CLI regressions |
| Historical public corpus | **12/12 snapshots passed** | Six known buggy snapshots produce the declared finding; six fixed snapshots do not |
| DeepSeek blind resolver A/B | **6/6 trials passed** | Two target-excluded, reference-backed Go protocols survived without repair |
| Independent negative in that A/B | **0 findings** | The accepted protocols did not fire on the clean negative fixture |
| Unsupported blind cases | **2/4 cases** | Gin and Terraform abstain because no admissible reference survives target exclusion |

The `6/6` number is **not a general bug-detection accuracy score**. It covers three trials over each of two Go resource-protocol cases. The historical corpus demonstrates deterministic expression of known defects, not unknown-bug recall.

## Run the offline evidence-loop demo

No API key or network access is required:

```bash
python -m smartbench.experiments.evidence_loop_demo \
  --output /tmp/smartbench-evidence-loop-demo.json
```

The scripted hypothesis Agent deliberately proposes the wrong cleanup method. The terminal summary then shows:

```text
initial hypothesis rejected
  → one bounded repair
  → evidence resolved
  → validator supported
  → cfg_dominance_between_acquire_and_use witness
  → before=1 / after=0 / negative=0
```

This is an offline **mechanism demo**, not a simulated claim about LLM quality. Live-model trials are reported separately.

Run the second-language, second-category security case:

```bash
smartbench benchmark run \
  --manifest benchmarks/real/requests_proxy_authorization_guard/manifest.yaml \
  --output /tmp/requests-security.json
```

It evaluates the Python fix for Requests `GHSA-j8r2-6x86-q33q` as a language-neutral `call → guard → assign` state invariant and produces `before=1 / after=0`.

## What works today

- **Semantic frontends:** Python and Go provide the deepest normalized operations and control-flow support; JavaScript and TypeScript share a partial SemanticIR frontend.
- **Deterministic analysis:** CFG, bounded ICFG, conservative call/data linking, declarative state invariants, resource lifecycle analysis, and provenance-aware findings.
- **Evidence boundary:** content-addressed EvidencePacks, source locations, graph snapshot hashes, unique-match evidence resolution, and explicit unknown/ambiguous states.
- **Agent review:** ProjectReader hypothesis generation plus evidence-exclusive Proposer, Critique, and Judge roles. Malformed or unsupported output does not become consensus.
- **Outputs:** JSON, SARIF, benchmark reports, analysis capability status, repository zone, source role, and semantic/heuristic derivation labels.
- **Repository handling:** mixed-language detection, bounded discovery, Git URL worktrees, dependency pruning, path confinement, and read-only normal analysis.

## Language coverage

| Language | Current level | Honest boundary |
| --- | --- | --- |
| Python | Semantic, deepest | CFG/ICFG, calls, state rules, partial data/type semantics |
| Go | Semantic, deepest | CFG/ICFG, state/resource analysis, surface TypeEvidence; no `go/types` |
| JavaScript / TypeScript | Semantic, partial | Shared statements/calls/control flow; async, exceptions, and dynamic dispatch remain partial |
| Rust | Structural | Tree-sitter symbols and graph context; no full semantic lowering |
| Java, Kotlin, C/C++, Ruby, Swift, C#, Zig | Detection / heuristic | Project fingerprinting and fallback structure, not compiler-grade analysis |

Adding a semantic language means implementing the frontend contract. Language-neutral analyzers must not import that language's parser. New frontends require stable source locations, explicit capabilities, deterministic IR serialization, and at least one before/after benchmark.

## Installation

Requirements: Python 3.10 or newer and Git.

```bash
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench
python -m venv .venv
source .venv/bin/activate
python -m pip install -e ".[graph]"
smartbench --help
```

The `graph` extra installs the Python, Go, JavaScript, TypeScript, and Rust tree-sitter parsers.

## Analyze a repository

Run deterministic multi-language diagnosis:

```bash
smartbench unified run \
  --project /path/to/repository \
  --output report.json \
  --sarif report.sarif
```

Select languages or rules:

```bash
smartbench unified rules
smartbench unified languages

smartbench unified run \
  --project /path/to/repository \
  --language python \
  --rule null_dereference \
  --rule security_data_flow \
  --output report.json
```

Run optional Agent review with an environment-configured provider:

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project /path/to/repository \
  --concern "find correctness, state, and concurrency risks" \
  --output agent-report.json
```

Supported credential variables include `DEEPSEEK_API_KEY`, `OPENAI_API_KEY`, `ANTHROPIC_API_KEY`, `GLM_API_KEY`, `DOUBAO_API_KEY`, `MOONSHOT_API_KEY`, and `DASHSCOPE_API_KEY`. Keys remain in process memory and are not written to reports.

## Reading the result honestly

| Status | Meaning |
| --- | --- |
| `full` | The requested rule requirements are fully available for the relevant language |
| `partial` | A bounded approximation ran; missing semantics are recorded |
| `unsupported` | Required capabilities are unavailable; this is not a clean result |
| `unknown` | The rule did not declare enough semantic requirements to claim coverage |
| `abstain` | The analyzer found missing, conflicting, ambiguous, or ownership-transfer evidence |

Findings also record source role (`production`, `test`, `fixture`, `generated`, and others), repository zone (`first_party`, `legacy`, `third_party`, `vendored`, or `generated`), and derivation method (`semantic` or `heuristic`).

## Historical benchmark corpus

| Project | Language | Public fix | Category |
| --- | --- | --- | --- |
| Requests | Python | [GHSA-j8r2-6x86-q33q](https://github.com/advisories/GHSA-j8r2-6x86-q33q) | Security state guard |
| FastAPI | Python | [#5465](https://github.com/fastapi/fastapi/pull/5465) | Resource lifecycle |
| Prometheus | Go | [#1070](https://github.com/prometheus/prometheus/pull/1070) | Resource lifecycle |
| Kubernetes | Go | [#29495](https://github.com/kubernetes/kubernetes/pull/29495) | Resource lifecycle |
| Gin | Go | [#4422](https://github.com/gin-gonic/gin/pull/4422) | Resource lifecycle |
| Terraform | Go | [#38585](https://github.com/hashicorp/terraform/pull/38585) | Resource lifecycle |

Run all pinned before/after snapshots:

```bash
smartbench benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

Each manifest records its upstream repository, commits, expected behavior, rule ID, and fixture boundary. See [the corpus documentation](benchmarks/real/README.md).

## Live blind ProjectReader experiment

This manual experiment incurs provider cost and is intentionally excluded from CI:

```bash
python -m smartbench.experiments.project_reader_blind_online \
  --benchmark-manifest benchmarks/real/manifest.yaml \
  --blind-manifest benchmarks/experiments/project_reader_blind/manifest.yaml \
  --negative-path benchmarks/experiments/project_reader_resource/negative \
  --trials 3 \
  --max-repairs 0 \
  --output project-reader-blind-online.json
```

The model sees pinned, hashed reference inventories with historical target paths excluded. Reports retain provider/model names, resolution decisions, before/after witnesses, abstentions, and negative results, but never API keys, raw prompts, or raw responses.

## Safety boundary

- Normal diagnosis does not edit the analyzed repository, open Issues, create pull requests, or contact maintainers.
- Repository content and metadata are treated as untrusted prompt data, but prompt marking is not a formal sandbox.
- Symlink and `../` escapes are ignored; external commands are invoked without a shell and have time/output bounds.
- Optional local diagnostics may run installed compilers or analyzers. Analyze only repositories you trust.
- `quick --sandbox` applies candidate patches only to a temporary copy, but repository tests still run with the current user's OS permissions.
- Never expose a repository containing secrets to a remote model provider unless that exposure is acceptable.

## Documentation

- [Architecture](docs/ARCHITECTURE.md)
- [Usage guide](docs/USAGE_GUIDE.md)
- [Live demo timeline (Chinese)](docs/DEMO_3_MINUTES_CN.md)
- [Evidence-loop architecture article](docs/EVIDENCE_LOOP_ARTICLE_CN.md)
- [Blind transfer experiment](benchmarks/experiments/project_reader_blind/README.md)
- [Historical benchmark corpus](benchmarks/real/README.md)

## Development

```bash
python -m pip install -e ".[dev,graph]"
ruff check smartbench tests
pytest -q
python -m compileall -q smartbench
python -m build
```

CI runs lint, compilation, and tests on Python 3.10–3.12, exercises all parser adapters, runs historical and ProjectReader benchmarks, builds wheel/sdist artifacts, and smoke-tests the installed CLI outside the checkout.

## Project status

SmartBench is ready for controlled real-repository audits, portfolio demonstrations, and architecture research. It is not yet a general unknown-bug detector or production SAST system. The next milestones are:

1. audit 5–8 independent repositories and publish verified, candidate, and abstained outcomes;
2. expand the blind corpus to 10–20 external before/after cases and natural negatives;
3. add a second Agent-discovered protocol category and another semantic language case;
4. measure precision, recall, abstention rate, trial stability, latency, and provider cost;
5. deepen exception, async, type, alias, and concurrency semantics without weakening the IR boundary.

No SmartBench finding should be filed upstream without repeated verification and an explicit human decision.

## License

[MIT](LICENSE)
