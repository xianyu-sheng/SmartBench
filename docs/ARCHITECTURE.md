# SmartBench Architecture

SmartBench is organized as a compiler-like analysis platform. The primary CLI
entry points open one `AnalysisSession`: language frontends lower a bounded
repository scan into versioned SemanticIR, the semantic linker runs once, and
deterministic analyzers, retrieval, ProjectReader, and review Agents consume
the same session state.

```text
source project
    -> shared ScanPlan -> language frontends -> SemanticIR (v1)
    -> SemanticLinker -> AnalysisSession
         |-> built-in / declarative rules
         |-> deterministic GraphRAG
         |-> ProjectReader hypothesis
               -> resolver -> validator -> CFG lifecycle analyzer
         `-> EvidencePack {facts, hypotheses, source references}
               -> proposer -> source verifier -> critic -> judge
    -> Finding + JSON / SARIF / benchmark report
```

This is one analysis boundary with several consumers, not one algorithm. The
commands differ only in which consumers they enable:

| Entry | Session consumer |
| --- | --- |
| `unified run` | Full SemanticIR, linker, deterministic rules and reports |
| `quick` / interactive wizard | The same result plus ProjectReader, retrieval and review Agents |
| `benchmark run` | The same result over pinned snapshots and declared expectations |
| `eval-rag` | Retrieval evaluation over the same session IR |
| `diagnose` | Separate local tool/process probes; it is not a semantic-analysis path |

`SemanticIR.from_graph` remains a compatibility wrapper for existing library
callers and bounded fallback behavior. The primary CLI no longer rebuilds that
shallow wrapper after already running a language frontend.

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
- source-unit metadata, deterministic provenance roles, and bounded project
  file access;
- detected languages;
- a capability matrix;
- versioned semantic facts;
- normalized operations and control-flow edges;
- language-neutral, provenance-bearing `TypeEvidence` assertions;
- a schema version (`semantic-ir/v1`).

Interprocedural operations additionally conform to
`semantic-ir/contracts/v1`: functions expose typed signature metadata,
parameters expose positions and receiver status, assignments expose aligned
bindings, calls expose argument/result bindings and a host operation, and
returns expose value lists. Contract validation checks shape and alignment;
empty types remain explicit unknowns rather than being treated as proof.

`semantic-ir/type-evidence/v1` separates portable type assertions from each
language's extraction mechanism. Evidence is attached to an operation and a
semantic role (`binding`, `receiver`, or `result`) and records its provider,
derivation source, canonical symbol, confidence, and exact source references.
Consumers query this contract through `TypeEvidenceIndex`; they do not import a
language parser. Type compatibility currently requires exact normalized
identity. Suffix similarity is never treated as type proof.

Rules declare minimum capability strength. The engine evaluates each relevant
language as `full`, `partial`, `unsupported`, or `unknown`; the last value is
used when a rule has not declared a semantic capability contract and must not
be upgraded to `full`. The report also records the rule's derivation method
(`heuristic` or `semantic`). Unsupported rules are never treated as clean.
Source roles (test, fixture, generated, and so on) are kept separate from
repository zones (`first_party`, `legacy`, `third_party`, `vendored`, and
`generated`) so a legacy production file cannot be mistaken for current
first-party production code.

### Deterministic graph retrieval

`smartbench.graph.evidence.DeterministicGraphRAG` performs stable lexical seed
selection, bounded graph traversal, deterministic ranking, and source-location
collection. It returns an `EvidencePack` containing:

- graph facts;
- stable content-addressed fact IDs;
- explicitly untrusted hypotheses kept outside the fact set;
- source references and snippets;
- retrieval trace;
- graph snapshot hash.

Only the pack's `facts` collection is a factual boundary. `hypotheses` contains
ProjectReader interpretations or heuristic diagnostic candidates with separate
`hypothesis-*` IDs. Later Agents may use them to choose what to inspect, but
the evidence gate never accepts a hypothesis ID as a fact ID.

### Project interpretation boundary

Project-specific conventions should not be hard-coded into language frontends
or converted directly into findings. `ProjectReaderAgent` may propose a bounded
`ProjectModel`, including resource-protocol candidates, over a deterministic
project inventory. Its output remains an untrusted hypothesis and never writes
facts into `SemanticIR`.

The resource-lifecycle path is deliberately split into four trust levels:

```text
ProjectReader hypothesis
    -> deterministic unique-match evidence resolver
    -> exact operation / type / fact validation gate
    -> project-protocol validation
    -> language-neutral CFG/dominance analysis
    -> finding or explicit abstention
```

The model selects semantic fields such as the operation, result position,
cleanup methods and member path; it does not need to copy opaque evidence IDs.
`DeterministicEvidenceResolver` binds the primary call fact, each reachable
cleanup-registration fact and portable type-evidence IDs. Zero matches produce
an `unresolved` abstention and multiple matches produce an `ambiguous`
abstention. Agent-cited IDs from older clients remain visible for audit but are
never consumed as resolved evidence.

When the selected real CALL operation and the Agent's redundant symbol spelling
differ, the resolver uses the real operation target and records both values in
`selector_normalizations`. This removes a mechanical spelling failure without
weakening cleanup, binding, reachability, type, or CFG validation.

In `quick` and the interactive wizard this path runs over the current
`AnalysisSession`. A supported project protocol may therefore be learned from
a successful usage and checked against other matching calls in the same
repository. If the inventory has no supported exemplar, the stage abstains.
The stricter cross-snapshot and target-excluded variants remain experiment
runners under `smartbench.experiments`.

For a resource protocol to pass the unchanged validator, the acquire call,
result position, and every cleanup method must be backed by resolved real
inventory facts and operations.
The cleanup registration must be reachable from the cited acquire call and act
on its selected result binding. A structurally valid mapping is still described
as project-scoped semantic evidence, not a universal language fact. Invented
operation IDs, unresolved or ambiguous evidence, unsupported schema fields,
and ungrounded cleanup methods are rejected before deterministic analysis.

`ResourceLifecycleAnalyzer` consumes only the validated portable protocol and
normalized operations. It checks whether cleanup registration dominates each
reachable use after acquisition. Missing uses, unusable result bindings, absent
symbols, and explicit ownership transfer to the caller produce abstentions
rather than findings. The current proof policy covers normalized defer-style
cleanup registration; other cleanup mechanisms remain unknown.

Validated resource protocols have three acquire match modes. `exact` requires
the same normalized call symbol. `typed_method` may transfer across receiver
spellings only when the normalized receiver type and canonical method symbol
are uniquely proven and identical on the reference and target calls, while the
result position, resource member path, cleanup method, and target-side member
use also agree. Both sides retain their source type-evidence IDs in the finding
witness. `method_shape` is the explicit partial fallback when receiver type is
unavailable. It cannot bypass stronger available type evidence, and method name
alone is never sufficient. Missing, ambiguous, or incompatible receiver types
produce abstentions.

### Interprocedural and concurrency linking

`smartbench.analysis.SemanticLinker` builds conservative operation-level call
edges after language IRs are merged. It resolves exact qualified symbols,
same-namespace simple calls, Python lexical `self`/`cls` calls, and globally
unique simple names. Python annotations, Go surface types, constructor syntax,
and resolved function return types can prove a receiver type; the linker then
iterates until no additional typed call can be resolved. Ambiguous names and
dotted receivers without unique type proof remain unresolved rather than
receiving invented edges. `CALL`, `SPAWN`, and `DEFER` share this contract.

The normalized interprocedural attribute contract is language-independent:
functions expose return types, parameters expose position and declared type,
assignments expose aligned bindings, and calls expose arguments, receiver and
result targets. A uniquely resolved call produces `DATA_DEPENDENCY` edges for
`argument_to_parameter` and `return_to_call`. `InterproceduralGraph` exposes
read-only callers, callees, bindings, returns and shortest call-path queries;
call-path depth is explicitly bounded and does not pretend to be whole-program
dominance.

`InterproceduralControlFlowGraph` adds a bounded, stack-consistent view over
the intraprocedural CFG. Embedded calls cannot be bypassed by a direct host to
continuation edge; their continuation is reintroduced only by a matching
`CALL_RETURN`. `InterproceduralStatePathQuery` exposes event-to-action paths
across function scopes as source-backed `STATE_TRANSITION` facts. It does not
turn path existence into an invariant violation: dominance and guard claims
remain the responsibility of a state analyzer with an explicit proof policy.

Go channel sends and receives expose a normalized `channel` attribute. The
linker emits `SYNCHRONIZES` edges only for matching channel expressions inside
the same function; interprocedural channel alias analysis remains explicitly
unsupported. Linked call, data-flow, and synchronization facts are included in
deterministic graph versions and are retrievable by EvidencePack queries.

### Multi-agent review

The `quick` and interactive review path uses `EvidencePolicy.EXCLUSIVE`:
arbitrary repository context is removed from proposer/critic/judge prompts,
and the same pack is used in every round. Proposed and final suggestions must
cite valid `fact-*` IDs; missing or unknown IDs are removed by a deterministic
membership gate. The source verifier checks locations and selected structural
relations through the session's SemanticIR compatibility view.

These checks have deliberately limited meanings. Fact-ID membership does not
prove that a cited fact logically entails the Agent conclusion, and a
`verified` location does not prove that a Bug exists. In the current JSON
contract `consensus_reached` means the Judge produced schema-valid JSON; it is
not a statistical multi-model agreement score.

## Current migration state

- `AnalysisSession` is now the primary runtime boundary. `unified`, `quick`,
  the interactive wizard, benchmark runner and RAG evaluator reuse it instead
  of building separate shallow and semantic graphs.
- `quick` runs ProjectReader over the complete session IR, keeps its output as
  hypotheses, resolves and validates resource protocols, and merges accepted
  CFG findings into the deterministic report before building the Agent pack.
- ProjectReader parses candidates independently. One malformed candidate is
  retained as an uncertainty and cannot discard other schema-valid candidates;
  top-level schema expansion still rejects the complete document.

- Python, Go, JavaScript, and TypeScript lower into the same normalized
  operation model. JavaScript and TypeScript share one frontend; their common
  statement/call surface is normalized while async scheduling, exceptions,
  dynamic dispatch, and type-checker facts remain explicit partial capability.
- Bounded discovery collects candidates across the repository before applying
  provenance-aware deterministic selection. Generated schema trees therefore
  cannot starve authored packages merely because they sort first.
- Existing CodeGraph consumers remain compatible through delegated graph
  queries.
- Unified results carry bounded EvidencePacks by default; use
  `--no-evidence` or `--max-evidence-packs` to control output size.
- Go is recognized by the structural frontend and lowers functions,
  typed parameters/receivers/results, assignments, calls, branches, loops,
  returns, goroutines, defer, channel send/receive and select into the common
  operation model. Its surface type provider emits declaration, struct-field,
  local-propagation, receiver, and local-result evidence and canonicalizes
  explicit import aliases. Type, data-flow, state/event and concurrency
  capabilities remain explicitly partial because SmartBench does not run
  `go/types`, resolve whole-program aliases, or model runtime dispatch.
- The operation call graph is deliberately partial. Current SmartBench
  self-analysis resolves conservative call edges and reports unresolved and
  ambiguous counts in `ir.meta.semantic_linker`; these are coverage signals,
  not clean-result claims.
- The ICFG is deliberately bounded and synchronous. It does not yet model
  callbacks, recursion summaries, dynamic dispatch, exceptions, goroutine
  happens-before, or channel aliases; those cases remain unknown.

### Declarative state-machine analysis

`smartbench.analysis.StateMachineAnalyzer` evaluates reusable invariants over
normalized operations. Its guard checks use intraprocedural CFG reachability,
dominance, and branch control dependence; source order alone is not treated as
proof. Invariants select an event, an action and an optional guard/exit
relation; the engine contains no project-specific identifiers. Repository-
specific expectations belong in benchmark specifications or rules, not in the
language frontend.

Rule files use the `smartbench.state-rules/v1` YAML schema and are loaded with
the repeatable `--state-rules` CLI option. A validated rule is adapted to the
normal `DiagnosticRule` interface, so its violations pass through confidence
filtering, JSON/SARIF output, and exact EvidencePack construction like built-in
rules. See `benchmarks/reasonix/reasoning_stop.yaml` for a real pre-fix/post-fix
specification.

State invariants default to `scope: intraprocedural`, preserving the original
CFG proof policy. A rule may opt into `scope: interprocedural` with a bounded
`max_call_depth`; guard-before-action proofs then require a matching guard on
the ICFG witness path and a same-scope CFG dominance/branch-control proof for
the action. A present but unproven caller-side guard is reported as unknown,
not as a false violation.

`smartbench.benchmarks.BenchmarkRunner` executes declared repository snapshots
through the same engine and checks finding-count/rule-ID expectations. The
`smartbench benchmark run --manifest ...` command emits machine-readable
pass/fail results without mutating a repository or creating a worktree.

The separate ProjectReader resource experiment learns project protocols from
fixed, source-backed reference snapshots and applies one generic lifecycle
analyzer to four historical Go before/after cases. This is a reproducible test
of the hypothesis/validation/analyzer seam, not evidence that an online model
autonomously discovers protocols. The experiment also evaluates an independent
clean fixture and records abstentions. Both historical benchmark reports are
CI artifacts.

`smartbench.experiments.project_reader_online` is the corresponding manual
online experiment. It replaces deterministic reference extraction with a real
environment-configured provider but retains the same inventory, evidence resolver,
protocol validator, analyzer, and acceptance corpus. Reports contain only
provider/model names, parsed decision statistics, and deterministic outcomes;
API keys, raw prompts, and raw model responses are not persisted. Missing
provider configuration is an explicit `unavailable` result, never a successful
abstention. Because external model behavior is nondeterministic and incurs
cost, this online experiment is not a required CI check.

`smartbench.experiments.project_reader_blind_online` is the stricter live-model
experiment. The model receives only pinned, hashed reference inventories from
which every historical target path has been excluded. Before/after snapshots
are parsed for deterministic evaluation but are never placed in the model
prompt. Multiple trials expose extraction instability instead of averaging it
away: a reference-backed case passes only when every requested trial reproduces
the expected before finding, clean after state, and independent-negative
result. Cases without an admissible reference do not invoke the model and
remain explicit unsupported outcomes. Missing provider configuration is
reported as unavailable, never replaced with a deterministic stand-in.

The live blind runner optionally permits a bounded evidence-feedback repair
when every initial candidate is rejected. It returns the same blind inventory,
the untrusted previous structured model, and deterministic validation reasons
to ProjectReader; no target snapshot or analyzer outcome is included. The full
replacement model then passes through the unchanged resolver and validator. Initial
rejections, repair attempts, and recoveries remain visible in the report rather
than being collapsed into the final result.

In the 2026-07-29 DeepSeek blind A/B, resolver-only mode (`max_repair=0`)
completed 6/6 trials across the two admissible reference-backed cases, with
zero rejected candidates and zero independent-negative findings. The previous
no-repair baseline completed 3/6 because opaque cleanup fact IDs were copied
incompletely. This is evidence for the resolver boundary on this small corpus,
not a general bug-detection accuracy claim.

The blind transfer experiment removes each historical target file and fix from
the reference inventory. Pinned, hashed files from unrelated project modules
provide admissible positive evidence. On the current four-case Go resource
corpus, exact symbol transfer detects one case and generalized transfer detects
two. The Prometheus transfer is now a `typed_method` match: both the unrelated
current reference and historical target resolve to `net/http.Client.Do` through
independent source-backed receiver chains. Both detected cases remain clean after their historical fix and the
independent negative fixture produces no finding. Gin and Terraform remain
unsupported because no same-project reference survives target exclusion. The
reported 50% coverage is a deliberate partial result, not a clean-project or
general recall claim.

## Acceptance criteria for new frontends

Before a language is marked semantically supported, its adapter must provide:

1. deterministic source-location round trips;
2. a capability declaration with explicit partial/unknown semantics;
3. stable SemanticIR serialization for the same source snapshot;
4. at least one pre-fix/post-fix known-bug benchmark;
5. no changes to language-neutral analyzers for basic rule execution.

Optional language-specific type providers must emit the shared TypeEvidence
contract and retain unsupported or ambiguous types as unknown. They may improve
portable analyzers without changing their language-neutral matching policy.
