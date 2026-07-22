# Changelog

All notable changes to SmartBench are documented here. The project follows
semantic versioning while it remains in beta.

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
