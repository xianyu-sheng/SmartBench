# SmartBench

[![CI](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml/badge.svg)](https://github.com/xianyu-sheng/SmartBench/actions/workflows/ci.yml)
[![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-3776AB.svg)](https://www.python.org/)
[![Release](https://img.shields.io/github/v/release/xianyu-sheng/SmartBench?label=release&color=4C1)](https://github.com/xianyu-sheng/SmartBench/releases)
[![Status: Public Beta](https://img.shields.io/badge/status-public_beta-orange.svg)](#project-status)
[![License: MIT](https://img.shields.io/badge/license-MIT-blue.svg)](LICENSE)

SmartBench is an experimental code-diagnosis workbench that combines normalized static analysis with evidence-constrained LLM review.

[中文说明](README_CN.md) · [Live demo](https://xianyu-sheng.github.io/SmartBench/) · [Architecture](docs/ARCHITECTURE.md) · [Usage](docs/USAGE_GUIDE.md)

[![SmartBench terminal demo](docs/assets/smartbench-demo-poster.png)](https://xianyu-sheng.github.io/SmartBench/)

The normal workflow is read-only. SmartBench does not edit the analyzed repository, open an Issue or pull request, or contact a maintainer unless a user performs those actions separately.

> [!IMPORTANT]
> SmartBench is a public Beta, not a production SAST replacement. It can run controlled repository audits and reproduce a small corpus of known defects. It has not established general unknown-bug precision or recall.

## What SmartBench is testing

SmartBench explores a specific division of responsibility in code diagnosis:

- language frontends and deterministic analyzers own source facts;
- an LLM may propose repository-specific conventions or risks as hypotheses;
- resolvers and validators decide whether a hypothesis can be bound back to
  source operations, types, and control flow;
- unsupported claims remain visible as `unknown` or `abstained` instead of
  being promoted to findings.

This is useful when a project convention is too local to justify a new
language-wide rule, but allowing a model to assert a bug directly would be too
weak a trust boundary.

What the current repository provides:

| Path | Output today | What it does not establish |
| --- | --- | --- |
| Deterministic `unified` analysis | Rule findings, capabilities, source roles, graph facts, JSON and SARIF | That a clean result means bug-free code |
| `quick` with a configured model | Project hypotheses, evidence-gated review, explicit rejection and abstention states | That an Agent conclusion is proved merely because it cites a real fact |
| Public before/after corpus | Reproducible checks that selected analyzers distinguish six known fixes | Precision or recall on previously unknown bugs |

The intended user today is someone evaluating the architecture or performing a
controlled, human-reviewed repository audit. It is not yet a drop-in CI quality
gate.

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

To gate a CI job on the result, opt in with `--fail-on`:

```bash
smartbench unified run --project . --fail-on warning
```

| Exit code | Meaning |
| --- | --- |
| `0` | No findings at or above the `--fail-on` threshold |
| `1` | Findings at or above the threshold |
| `2` | Invalid arguments or an internal error |

`--fail-on` accepts `none` (default), `info`, `warning`, or `error`. The default
`none` always exits `0`, so adding the flag is required to change behavior.

For byte-stable JSON suitable for golden files or diffs, add
`--deterministic-output`. It normalizes runtime-dependent report fields while
leaving the default output unchanged:

```bash
smartbench unified run --project . --output report.json --deterministic-output
```

Run the interactive evidence/Agent path:

```bash
export DEEPSEEK_API_KEY="your-key"
smartbench quick \
  --project /path/to/repository \
  --concern "find correctness and resource-lifecycle risks" \
  --output agent-report.json
```

### Before submitting an upstream finding: check active branches

A valid finding on the default branch may already be fixed on a project's
active `dev`, `develop`, `next`, or `release` branch. Run this gate before
opening an issue:

```bash
smartbench check-branches \
  --input agent-report.json \
  --repo /path/to/repository
```

The checker accepts both Quick-mode `path:line` locations and structured
locations. It checks fetched local/remote branches and, for a shallow clone,
fetches the tip of missing active branches only (without switching branches or
modifying worktree files). An `Already-fixed` result means **do not submit**;
review the upstream branch or its pending pull request instead.

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

The current debate gate validates fact-ID existence, not logical entailment. A
model can still cite a real fact that does not support its conclusion. Location
verification, deterministic ProjectReader validation, and human review are
therefore separate requirements; typed conclusion-to-evidence relations remain
open work.

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
- `consensus_reached` currently means that Proposer, Critique, and Judge all
  returned schema-valid output. It is a stage-completion flag, not a statistical
  agreement score between independent models;
- if Critique or Judge fails, Proposer or Judge output is retained under
  `unreviewed_suggestions` for audit but is not promoted to `final_suggestions`.
  The console reports an incomplete review rather than a clean result.

## Reproducible evaluation

The repository contains 20 before/after cases (36 minimal source snapshots:
18 in `benchmarks/real/`, 1 interprocedural, 1 reasonix) derived from public
fixes and SmartBench-discovered bugs. These fixtures preserve the code
needed by the declared analyzer; they are not complete historical
repository checkouts:

| Project | Language | Public fix | Category |
| --- | --- | --- | --- |
| Requests | Python | [GHSA-j8r2-6x86-q33q](https://github.com/advisories/GHSA-j8r2-6x86-q33q) | Security state guard |
| FastAPI | Python | [#5465](https://github.com/fastapi/fastapi/pull/5465) | Resource lifecycle |
| Prometheus | Go | [#1070](https://github.com/prometheus/prometheus/pull/1070) | Resource lifecycle |
| Kubernetes | Go | [#29495](https://github.com/kubernetes/kubernetes/pull/29495) | Resource lifecycle |
| Gin | Go | [#4422](https://github.com/gin-gonic/gin/pull/4422) | Resource lifecycle |
| Terraform | Go | [#38585](https://github.com/hashicorp/terraform/pull/38585) | Resource lifecycle |
| Sniproxy | Go | [PR #203](https://github.com/mosajjal/sniproxy/pull/203) (SmartBench) | Configuration validation |
| Qscan | Go | [Issue #22](https://github.com/qi4L/qscan/issues/22) (SmartBench) | Resource lifecycle |
| Stunner | Go | [Issue #89](https://github.com/firefart/stunner/issues/89) (SmartBench) | Resource lifecycle |

Plus nine synthetic common-pattern fixtures (HTTP body close, SQL rows
close, file copy close, buffered writer flush, ticker stop, mutex unlock,
context cancel, websocket close, requests session close) that pin the
state-rule engine against recurring defect shapes.

```bash
smartbench benchmark run \
  --manifest benchmarks/real/manifest.yaml \
  --output benchmark-report.json
```

The current expected result is 36/36 snapshot checks: each declared buggy
snapshot produces the expected rule and each fixed snapshot produces none.
This shows that SmartBench can express these known defects. It does not
measure unknown-bug recall.

The separate blind ProjectReader experiment excludes historical target files from the model inventory. A recorded DeepSeek A/B completed 6/6 trials over two admissible Go protocols after deterministic ID resolution; two other cases remained unsupported because no admissible reference survived exclusion. See [the experiment notes](benchmarks/experiments/project_reader_blind/README.md) for the exact boundary.

## Known limitations

- Most built-in rules are still source heuristics; declarative state rules and the resource-lifecycle analyzer provide the strongest semantic examples.
- Exception flow, async scheduling, dynamic dispatch, alias analysis, goroutine happens-before, and interprocedural channel aliases are incomplete.
- The ProjectReader lifecycle analyzer currently proves normalized defer-style cleanup only.
- The benchmark corpus is small and dominated by resource-lifecycle cases.
- Large-repository latency and memory use do not yet have a published budget.
- Local vector/TF-IDF caches are not yet portable across every optional-
  dependency change; remove the repository's `.smartbench/` cache if a cache
  created with scikit-learn is later opened without it.
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
- [Type-evidence lesson: evaluation false positives → verifier fix](docs/TYPE_EVIDENCE_LESSON_CN.md)
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

## Real-world evaluation (2026-08-04, corrected 2026-08-04)

SmartBench was evaluated against 12 open-source repositories across Python
and Go, combining deterministic rules and LLM evidence-gated multi-agent
review (DeepSeek). 410 total findings were manually verified, then
cross-checked against SDK types and project documentation.

| Repository | Language | Stars | Deterministic | LLM Agent |
| --- | --- | --- | --- | --- |
| Flask | Python | 67k | 54 findings, 0 real | — |
| httpx | Python | 13k | 32 findings, 0 real | — |
| Bottle | Python | 8k | 21 findings, 0 real | 2 suggestions, 0 real |
| Litestar | Python | 5k | 269 findings, 0 real | — |
| resty | Go | 10k | 9 findings, 0 real | 3 suggestions, 0 real |
| Robyn | Python | 5k | — | 4 suggestions, 1 valid (test hygiene) |
| Reflex | Python | 20k | — | 1 suggestion, 0 real |
| PocketBase | Go | 43k | — | 3 suggestions, 0 real |
| Reasonix | Go | 80k+ | — | 1 suggestion, 0 real |
| Templ | Go | 8k | — | review failed (API timeout) |

After cross-checking SDK types and project documentation, **3 of the
4 initially-submitted findings were determined to be incorrect**:

| Initial finding | Repository | Correction |
| --- | --- | --- |
| Resource file handle not closed | Reasonix | SDK type is `io.Reader`, not `io.ReadCloser`; PR #7377 was a no-op and has been closed |
| `panic()` in backup restore | PocketBase | Intentional fail-stop design per function doc comment; not a bug |
| Filesystem handle leak | PocketBase | `NewFilesystem().Close()` returns nil immediately; no handle opened |

**1 confirmed valid observation** (test hygiene, not production bug):

| Observation | Repository | Upstream |
| --- | --- | --- |
| 16 `stream=True` calls in SSE tests without explicit `response.close()` | Robyn | [Issue #1432](https://github.com/sparckles/Robyn/issues/1432) |

All upstream threads have been updated with corrected assessments.

Key observations:
- Deterministic rules produced zero real bugs — the rules are conservative
  heuristics that don't hallucinate on clean code.
- The LLM evidence-gated path proposed plausible resource-lifecycle patterns,
  but cross-checking against SDK types and project documentation revealed that
  most were incorrectly grounded. Current location verification validates
  source-file existence but does not trace type definitions through
  dependencies.
- The abstention and evidence-gate mechanisms correctly rejected unsupported
  claims (e.g. path traversal, command injection).
- **The primary gap is not the Agent debate quality, but the verifier's
  inability to resolve types across dependency boundaries.** Strengthening
  cross-package type resolution would be the highest-leverage improvement.

### Re-run after the type-checker fix (2026-08-04)

The type-checker adapter (`tools/typeprobe` + `GoTypeCheckerProvider`) was
implemented to close that gap, then the same 10-repository evaluation was
re-run. Go repositories now emit `TYPE_CHECKER` evidence through
`go/packages` + `go/types`:

| Repository | Type-checker evidence | Closer types | LLM suggestions | Verified |
| --- | ---: | ---: | ---: | ---: |
| Flask | — | — | 0 | 0 |
| httpx | — | — | 1 | 0 |
| Bottle | — | — | 2 | 1 |
| Litestar | — | — | 1 | 1 |
| resty | 1,294 | 168 | 2 | 2 |
| Robyn | — | — | 2 | 1 |
| Reflex | — | — | 4 | 4 |
| PocketBase | 8,747 | 403 | 0 | 0 |
| Templ | 4,173 | 278 | 2 | 2 |
| Reasonix | 26,436 | 1,604 | 2 | 2 |
| **Total** | **40,650** | **2,453** | **16** | **13 (81%)** |

Before the fix the LLM path produced 18 suggestions with 4 confirmed real
(22%); after the fix, 13 of 16 suggestions pass verification (81%) with 0
rejected. The two previously-reported false positives disappeared:

- **Reasonix** no longer proposes closing `resp.File` — the resolved type
  is `io.Reader` with no Close method, and the CrossChecker `resource_type`
  claim verification rejects such claims.
- **PocketBase** produces no unverifiable resource-lifecycle suggestions
  (previous `panic()` and filesystem-handle findings were both refuted).

New verified findings include production-path resource leaks in resty
(multipart boundary failure leaks `mw`; JSON escape failure leaks a
buffer), a gzip writer leak in templ, and file leaks in Reasonix.
CrossChecker `resource_type` claims are now verified against type evidence
rather than location existence alone.

**Independent cross-check (codex + manual)**: the two resty findings were
subsequently reviewed and both were determined to be **false positives** —
`multipart.Writer` holds no OS resources (the pipe writer is already
closed on the failure path), and the `bodyBuf` has a global safety net
(`Request.Execute` unconditionally calls `backToBufPool(r.bodyBuf)`), so
the missing local `releaseBuffer` is an inconsistency, not a leak. This
confirms that internal verification passing (81%) measures claim
grounding, **not** real-bug precision; each candidate still requires
cross-dependency review before submission.

### Upstream submissions tracking (2026-08-04)

Findings confirmed by runtime verification (local fix → reproduce →
control → re-test) have been submitted upstream, credited to SmartBench.
A cron job watches these threads daily and reports state changes.

| Issue | Repository | Status | Finding | Verification |
| --- | --- | --- | --- | --- |
| [#22](https://github.com/qi4L/qscan/issues/22) | qi4L/qscan | OPEN | `CheckSID` leaks `*sql.DB` on every error path (missing `defer db.Close()`) | goroutine delta +5 per failed check → 0 after fix |
| [#89](https://github.com/firefart/stunner/issues/89) | firefart/stunner | CLOSED | `testPassword` leaks the TURN connection on every path (brute force accumulates sockets) | fix confirmed on `dev` branch — discovery correct, already patched upstream |
| [#1432](https://github.com/sparckles/Robyn/issues/1432) | sparckles/Robyn | OPEN | SSE tests use `stream=True` without closing response (test hygiene) | confirmed valid, low severity |
| [#203](https://github.com/mosajjal/sniproxy/pull/203) | mosajjal/sniproxy | PR OPEN | `refresh_interval: 0` crashes the process: `time.NewTicker` panics in the ACL refresh goroutine (cidr/domain/geoip) | reproduced panic at runtime → fix control → no panic, tests pass |

When an issue is closed as accepted (or a PR is merged), this table is
updated and the corresponding repository is added to the portfolio.

## Project status

SmartBench is suitable for controlled repository experiments, architecture research, and portfolio demonstrations. The next useful work is effect measurement, not more surface features:

1. expand the corpus to 10-20 external before/after cases and natural negatives;
2. add another Agent-derived protocol category and another semantic language case;
3. report precision, recall, abstention rate, trial stability, latency, and provider cost;
4. deepen exception, async, type, alias, and concurrency semantics without weakening the IR boundary.

## License

[MIT](LICENSE)
