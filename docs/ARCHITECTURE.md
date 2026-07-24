# SmartBench Architecture

SmartBench is organized as a compiler-like analysis platform. Language
frontends lower source projects into a versioned Semantic IR; deterministic
analyzers and graph retrieval consume that IR; agents only interpret and
challenge source-backed evidence.

```text
source project
    -> language frontend
    -> SemanticIR (v1)
    -> structural code graph + capability matrix
    -> deterministic analyzers / graph retrieval
    -> EvidencePack
    -> proposer / critic / judge / verifier
    -> Finding + SARIF / JSON report
```

## Boundaries

### Language frontend

`smartbench.core.adapters.LanguageAdapter` is the compatibility name for the
frontend interface. Every frontend provides:

- language and file extensions;
- structural parsing into `CodeGraph`;
- `semantic_capabilities`;
- `parse_semantic_file` and `parse_semantic_project`.

Adding a language should normally mean adding a frontend adapter. Core rules
must not import that language's parser.

### Semantic IR

`smartbench.ir.SemanticIR` is the stable analysis boundary. It contains:

- the compatibility structural graph;
- source-unit metadata and bounded project file access;
- detected languages;
- a capability matrix;
- versioned semantic facts;
- normalized operations and control-flow edges;
- a schema version (`semantic-ir/v1`).

Capability declarations are conservative. Unsupported information is reported
as missing/unknown instead of being treated as a clean result.

### Deterministic graph retrieval

`smartbench.graph.evidence.DeterministicGraphRAG` performs stable lexical seed
selection, bounded graph traversal, deterministic ranking, and source-location
collection. It returns an `EvidencePack` containing:

- graph facts;
- source references and snippets;
- retrieval trace;
- graph snapshot hash.

The pack is the factual boundary for later model calls. It is not a free-form
LLM summary.

### Multi-agent verification

`DebateEngine.debate(..., evidence_pack=pack)` injects the pack into the
proposer/critic/judge context. Agents may propose or reject hypotheses, but a
concrete claim must cite an evidence entry. Existing disk and graph verifiers
remain compatible with SemanticIR through its graph compatibility view.

## Current migration state

- Existing Python/JavaScript/TypeScript data-flow rules now receive SemanticIR
  through the unified engine boundary.
- Existing CodeGraph consumers remain compatible through delegated graph
  queries.
- Unified results carry bounded EvidencePacks by default; use
  `--no-evidence` or `--max-evidence-packs` to control output size.
- Go is recognized by the structural frontend but does not yet claim semantic
  type or interprocedural data-flow capabilities. Its first semantic frontend
  now lowers functions, parameters, assignments, calls, branches, loops,
  returns, goroutines, defer, channel send/receive and select into the common
  operation model. State/event and concurrency capabilities remain explicitly
  partial until interprocedural resolution is available.

### Declarative state-machine analysis

`smartbench.analysis.StateMachineAnalyzer` evaluates reusable invariants over
normalized operations. Invariants select an event, an action and an optional
guard/exit relation; the engine contains no project-specific identifiers.
Repository-specific expectations belong in benchmark specifications or rules,
not in the language frontend.

## Acceptance criteria for new frontends

Before a language is marked semantically supported, its adapter must provide:

1. deterministic source-location round trips;
2. a capability declaration with explicit partial/unknown semantics;
3. stable SemanticIR serialization for the same source snapshot;
4. at least one pre-fix/post-fix known-bug benchmark;
5. no changes to language-neutral analyzers for basic rule execution.
