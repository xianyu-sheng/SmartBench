# Changelog

All notable changes to SmartBench are documented here. The project follows
semantic versioning while it remains in beta.

## [Unreleased]

### Added

- Capability-strength contracts (`full`/`partial`/`unsupported`) for rules,
  with per-rule analysis status and limitation metadata in JSON results.
- Deterministic `SourceRole` provenance on `SemanticIR` source units, including
  a production-scoped policy for security data-flow findings.
- A shared JavaScript/TypeScript SemanticIR frontend for functions, parameters,
  assignments, branches, loops, returns, calls, and surface type annotations.
- Provenance-aware, repository-wide bounded file selection so one early
  language or generated-schema directory cannot starve later monorepo packages.
- Versioned `SemanticIR` contracts for normalized functions, parameters,
  bindings, calls, returns, and explicit capability/unknown reporting.
- Conservative Python/Go interprocedural linking for calls, arguments,
  returns, and same-function Go channel synchronization.
- A bounded, stack-consistent interprocedural control-flow graph (ICFG) and
  source-backed cross-function state paths.
- Declarative `scope: interprocedural` state rules with an explicit guard-proof
  policy that abstains when caller-side control cannot be proven.
- Deterministic graph facts and evidence metadata for linked calls, data-flow,
  synchronization, and state paths.
- Reproducible cross-function and Reasonix pre-fix/post-fix benchmark fixtures.

### Validation

- 547 tests passing with graph extras; 509 passing and 38 skipped without
  optional parsers on 2026-07-27.
- The cross-function benchmark reports before=1 and after=0; the Reasonix
  fixture detects the known pre-fix issue and not the fixed snapshot.
- Replayed the same ten public AI coding-agent repositories: all runs completed
  without parser errors; the new contract reduced only non-production data-flow
  findings (3 in Aider and 1 in SWE-agent) and did not suppress a confirmed
  production finding.
- Replayed those ten repositories after JS/TS lowering: total normalized
  operations increased from 109,342 to 220,476, all runs remained error-free,
  and every TypeScript-bearing repository now emits semantic operations.

These additions are deliberately conservative. Dynamic dispatch, complete type
checking, exception/async semantics, goroutine happens-before, channel aliases,
and broad multi-language semantic lowering remain future iterations.

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
