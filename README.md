<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT License" />
  <img src="https://img.shields.io/badge/tests-266%20passed-brightgreen?style=for-the-badge" alt="266 Tests Passed" />
  <img src="https://img.shields.io/badge/ruff-clean-cyan?style=for-the-badge" alt="Ruff Clean" />
  <a href="README_CN.md"><img src="https://img.shields.io/badge/read-中文文档-green?style=for-the-badge" alt="中文文档" /></a>
</p>

---

<h1 align="center">SmartBench — Universal Code Diagnostic Platform</h1>

<p align="center">
  <em>Multi-Agent Debate + Evidence Verification + Tool Execution = Diagnoses You Can Trust</em>
</p>

<p align="center">
  <b>SmartBench never modifies your code.</b> It analyzes, diagnoses, and recommends — you stay in full control.
</p>

---

## What is SmartBench?

SmartBench is an **LLM-powered code diagnostic tool** that analyzes any codebase through a structured multi-agent debate with **zero-hallucination evidence verification**. 

Every claim made by the AI must cite exact file paths and line numbers — these are verified against disk before reaching the final report.

```
$ smartbench
╔══════════════════════════════════════════════╗
║ SmartBench — Universal Code Diagnosis       ║
║ AI-powered analysis for any codebase        ║
╚══════════════════════════════════════════════╝

Step 1/4 — Where is your code?
Step 2/4 — Configure LLM API keys
Step 3/4 — Analyzing your project...
Step 4/4 — What would you like to diagnose?

[Proposer] → [Verifier] → [Critique] → [Judge] → Report
```

## How It Works

```
                        ┌─────────────┐
                        │   SmartBench │
                        └──────┬──────┘
                               │
          ┌────────────────────┼────────────────────┐
          ▼                    ▼                    ▼
   ┌──────────────┐    ┌──────────────┐    ┌──────────────┐
   │   Phase 1    │    │   Phase 4    │    │   Phase 5    │
   │  Fingerprint │    │  Code Graph  │    │   Debate     │
   │  (zero LLM)  │    │  + RAG Index │    │   Engine     │
   └──────────────┘    └──────────────┘    └──────┬───────┘
                                                  │
                    ┌─────────────────────────────┤
                    ▼                ▼            ▼
             ┌──────────┐   ┌──────────┐  ┌──────────┐
             │ Proposer │──▶│ Critique │─▶│  Judge   │
             │ (方案提出) │   │ (交叉审查) │  │ (最终仲裁) │
             └────┬─────┘   └────┬─────┘  └────┬─────┘
                  │              │              │
                  ▼              ▼              ▼
             ┌──────────────────────────────────────┐
             │         Verifier (Zero LLM)          │
             │   Disk I/O check on every claim      │
             └──────────────────────────────────────┘
```

### The Anti-Hallucination Guarantee

Every diagnosis goes through a **three-layer verification**:

1. **File Existence** — quoted file paths are checked against disk
2. **Line Accuracy** — quoted line numbers must match actual source
3. **Call Chain Integrity** — function relationships verified via code graph

Claims that fail verification are **flagged and downgraded** before reaching the final report.

## Quick Start

```bash
# Install
pip install -e .

# Set API key (at least one required)
export DEEPSEEK_API_KEY=sk-your-key

# Run
smartbench              # Interactive 4-step wizard
smartbench --quick      # Auto-detect everything
smartbench check        # Check available diagnostic tools
```

### CLI Reference

| Command | Description |
|---|---|
| `smartbench` | Interactive wizard: project → API keys → detect → diagnose |
| `smartbench --quick` | Non-interactive mode, uses env API keys |
| `smartbench quick --project ./my-repo` | Quick diagnosis on a specific project |
| `smartbench diagnose --project ./my-repo --symptoms "slow queries"` | Targeted diagnosis |
| `smartbench check` | Show available diagnostic tools for current directory |

## Supported Languages & Frameworks

### 14 Languages

Python · Go · Rust · C · C++ · Java · Kotlin · JavaScript · TypeScript · Ruby · Swift · C# · Zig · Mixed projects

Tree-sitter precision parsing for Python, Go, JavaScript, TypeScript, and Rust (regex fallback for others).

### 20+ Frameworks Auto-Detected

FastAPI · Flask · Django · Gin · Echo · Fiber · Express · NestJS · Next.js · React · Vue · Spring Boot · Axum · Actix · gRPC · and more

## Architecture

```
smartbench/
├── cli/             # CLI (104-line main, wizard, phases, display)
├── llm/             # Provider registry + API client (8 providers, retry logic)
├── detector/        # Zero-LLM project fingerprinting
├── graph/           # AST code graph (tree-sitter + regex), 14 languages
├── rag/             # Vector indexing (3-tier: transformers → TF-IDF → hash)
├── verifier/        # Evidence verification (disk I/O, zero LLM)
├── engine/          # Multi-agent debate engine (Proposer → Critique → Judge)
├── diagnostics/     # 30+ pluggable diagnostic tools
└── prompts/         # Dynamic prompt factory (language-specific guidance)
```

## Key Features

### Multi-Agent Debate Engine

Three specialized roles debate every diagnosis:

| Role | Responsibility |
|---|---|
| **Proposer** | Analyzes code context, proposes fixes with exact file paths and line numbers |
| **Critique** | Adversarial review — finds counterexamples, missing context, false positives |
| **Judge** | Synthesizes debate transcript into final report with consensus scoring |

### Evidence Verification (Zero LLM)

All claims are verified through **deterministic disk I/O** — no LLM involved:

- File paths checked against filesystem
- Line numbers validated against source
- Verdict scores: `verified` / `partial` / `hallucinated`
- Hallucination rate tracked and reported

### Code Graph + RAG

- **AST-level code graph**: functions, classes, call edges, imports
- **3-tier embedding**: sentence-transformers → TF-IDF → character hash (auto-degrade)
- **Vector store**: SimpleVectorStore (default) or ChromaDB (optional)
- **Semantic search**: natural language queries against indexed codebase

### Diagnostic Tool Execution

Tools are **actually executed** — not just suggested. Results injected into debate context:

| Strategy | Tools Executed |
|---|---|
| `performance_analysis` | py-spy, pprof, perf, flamegraph suggestions |
| `security_scan` | bandit, gosec, cargo-audit, npm audit suggestions |
| `correctness_audit` | ruff, mypy, go vet, clippy suggestions |
| `architecture_review` | Code graph cycle detection, coupling analysis |

### 8 LLM Providers

Auto-detected from model name. Role-aware routing: different models for Proposer / Critique / Judge.

DeepSeek · OpenAI · Anthropic · GLM · Doubao · Moonshot · Qwen · Ollama (local)

## Diagnosis Strategies

| Strategy | Focus |
|---|---|
| `performance_analysis` | CPU, memory, I/O profiling; hot path identification |
| `correctness_audit` | Bug detection, edge cases, error handling gaps |
| `architecture_review` | Design patterns, coupling, cohesion, modularity |
| `security_scan` | Injection, secrets exposure, dependency vulnerabilities |
| `hotspot_analysis` | High-churn files, complexity, bug density |

## Configuration

### Environment Variables

```bash
export DEEPSEEK_API_KEY=sk-...    # DeepSeek
export OPENAI_API_KEY=sk-...      # OpenAI
export ANTHROPIC_API_KEY=sk-...   # Anthropic
# ... or any of the 8 supported providers
```

### Optional Dependencies

```bash
pip install -e ".[dev]"     # pytest, ruff
pip install -e ".[graph]"   # tree-sitter for precise AST parsing
pip install -e ".[rag]"     # sentence-transformers + ChromaDB
```

## Testing

```bash
pytest tests/ -v            # 266 tests (unit + integration + CLI + E2E)
ruff check smartbench/       # Lint check (clean)
```

## FAQ

**Q: Does SmartBench modify my code?**
No. Read-only analysis. Output goes to a separate report.

**Q: How does it prevent AI hallucinations?**
Every file path and line number claimed by the LLM is verified against disk by a zero-LLM verifier. Hallucinated claims are flagged and downgraded.

**Q: Can it run in CI?**
SmartBench supports non-interactive mode: `smartbench quick --project .` with env API keys. JSON output mode coming soon.

**Q: What languages does it support?**
14 languages for analysis. Tree-sitter precision parsing for Python, Go, JS/TS, Rust. Regex-based parsing for the rest.

**Q: Do I need a GPU?**
No. The embedding engine auto-degrades from sentence-transformers → TF-IDF → character hash. LLM calls go to remote APIs. Fully CPU-compatible.

**Q: How much does it cost?**
Depends on your LLM provider. Local (Ollama): free. DeepSeek: ~$0.01/run. A typical diagnosis makes 4 LLM calls (strategy + 3 debate rounds).

## License

[MIT](LICENSE) © Xianyu Sheng

---

<p align="center">
  <a href="https://github.com/xianyu-sheng/SmartBench">GitHub</a> ·
  <a href="README_CN.md">中文文档</a> ·
  <a href="https://github.com/xianyu-sheng/SmartBench/issues">Issues</a>
</p>

<p align="center">
  <sub>Built with ❤️ for the open-source AI agent community</sub>
</p>
