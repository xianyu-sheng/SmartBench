# Changelog

All notable changes to SmartBench are documented here. The project follows
semantic versioning while it remains in beta.

## [Unreleased]

### Added

- `AnalysisSession`, a shared runtime boundary for repository discovery,
  complete SemanticIR lowering, semantic linking, rules, retrieval, and Agents.
- A ProjectReader stage in `quick` and the interactive wizard: untrusted
  project protocols pass through deterministic resolution, validation, and a
  language-neutral CFG lifecycle analyzer before becoming findings.
- Explicit `SemanticHypothesis` entries in EvidencePack. ProjectReader output
  and heuristic diagnostic candidates no longer share the `fact-*` namespace.
- Candidate-level ProjectReader schema isolation and auditable selector
  normalization for redundant symbol spellings.
- Capability-strength contracts, source roles, repository zones, shared
  JavaScript/TypeScript lowering, conservative semantic linking, bounded ICFG,
  declarative interprocedural state rules, and TypeEvidence contracts.
- Six public-fix-derived before/after cases covering 12 bounded source
  snapshots across Python and Go.

### Changed

- `unified`, `quick`, the interactive wizard, benchmark runner, and RAG
  evaluator now consume the shared session instead of rebuilding different IR
  depths. `SemanticIR.from_graph` remains a compatibility/fallback API.
- `quick` JSON embeds the deterministic session report under
  `analysis_report`; no-provider quick runs return the deterministic report
  instead of graph statistics only.
- Documentation now distinguishes fact membership, source verification,
  three-stage review completion, and actual semantic correctness.
- ProjectReader inventories now enforce a serialized-character budget while
  retaining complete scopes, prioritizing cleanup-bearing evidence, and
  sampling deterministically across repository files and ranges.

### Fixed

- Regex fallback graph construction masks comments and string literals before
  extracting declarations and calls, preventing common phantom nodes and call
  edges while preserving source offsets and import literals.
- Failed Critique or Judge stages no longer promote a Proposer fallback into
  final findings. Structured partial output is retained separately as
  `unreviewed_suggestions`, with explicit per-stage status.

### Validation

- The full graph-enabled test suite and Ruff checks pass locally.
- The historical benchmark passes all 12 declared snapshots: six buggy and six
  fixed.
- A real-provider integration smoke accepted one source-backed project protocol,
  rejected an ungrounded candidate, and produced one CFG witness on a bounded
  safe/unsafe fixture. This is a pipeline check, not an accuracy measurement.

Dynamic dispatch, complete type checking, exception/async semantics, goroutine
happens-before, channel aliases, and broad multi-language semantic lowering
remain future work.

## [0.7.0] - 2026-07-23

### Added

- **Unified multi-language diagnostic framework** (`smartbench unified`):
  - 10 built-in diagnostic rules: `null_dereference`, `resource_leak`,
    `hardcoded_secret`, `insecure_random`, `broad_exception`, `unused_import`,
    `todo_fixme`, `command_injection`, `sql_injection`, `path_traversal`.
  - 6 language adapters: Python, Go, Java, Rust, JavaScript, TypeScript.
  - Language-agnostic rule base with per-language adapter extensions.
  - `smartbench unified rules` and `smartbench unified languages` commands.
  - `smartbench unified run --project <path>` for fast static analysis.
  - Filter by specific rules (`--rule`) or languages (`--language`).

- **SARIF output**:
  - Standard SARIF 2.1.0 format for GitHub/GitLab code scanning integration.
  - `--sarif <file>` option to export findings in machine-readable format.
  - Rule metadata, confidence scores, and precise location reporting.

- **Core infrastructure**:
  - `DiagnosticRule` abstract base class with helper methods for source reading.
  - `LanguageAdapter` base with AST and regex-based node extraction.
  - `RuleRegistry` and `AdapterRegistry` for extensibility.
  - `UnifiedDiagnosticEngine` with configurable analysis pipeline.
  - 152 new tests, total 468 tests passing.

## [0.6.1] - 2026-07-22

### Fixed

- Bounded repository scans, source reads, prompt previews, RAG indexing, and
  local subprocess output so oversized or malformed inputs degrade safely.
- Rebuilt missing, incomplete, corrupt, stale, or configuration-incompatible
  RAG indexes instead of silently reusing them.
- Made retrieval metrics use structured retrieved-file evidence and respected
  configured graph/vector context weights.
- Isolated malformed model, verification, vector-search, diagnostic-tool, and
  CLI output failures instead of aborting the full diagnosis pipeline.
- Routed mixed-language diagnostics across detected languages and detected
  dependencies independently of Git metadata.
- Resolved call edges conservatively when function names are duplicated,
  respected real symbol boundaries, and preferred current source over archived
  or legacy implementations during retrieval.
- Corrected Python, Go, JavaScript, and TypeScript symbol/import extraction,
  including Python async functions and JS/TS arrow functions.
- Restricted opt-in patch verification to bounded trusted-repository copies,
  validated patch targets, and removed credential-like environment variables.
- Cloned Git URLs non-interactively, sanitized remote credentials, recognized
  worktrees and nested projects, and bounded external command execution.

### Added

- `smartbench --version`.
- Clean-wheel installation and CLI smoke tests in CI.
- Regression coverage for the repaired failure and resource-boundary paths.

## [0.6.0]

- Initial beta of the evidence-grounded repository diagnosis architecture.
