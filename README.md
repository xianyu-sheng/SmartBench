<p align="center">
  <img src="https://img.shields.io/badge/Python-3.10%2B-3776AB?style=for-the-badge&logo=python&logoColor=white" alt="Python 3.10+" />
  <img src="https://img.shields.io/badge/license-MIT-blue?style=for-the-badge" alt="MIT License" />
  <img src="https://img.shields.io/badge/status-beta-orange?style=for-the-badge" alt="Status: Beta" />
  <a href="https://www.youtube.com/watch?v=your-demo-video-id"><img src="https://img.shields.io/badge/demo-video-FF0000?style=for-the-badge&logo=youtube&logoColor=white" alt="Demo Video" /></a>
  <a href="README_CN.md"><img src="https://img.shields.io/badge/read-中文文档-green?style=for-the-badge&logo=readthedocs&logoColor=white" alt="中文文档" /></a>
</p>

---

<h1 align="center">SmartBench — Universal Code Diagnostic Platform</h1>

<p align="center">
  <em>Zero-Hallucination Code Analysis Powered by Multi-Agent Debate + RAG + Evidence Verification</em>
</p>

<p align="center">
  <strong>📖 中文文档 → <a href="README_CN.md">README_CN.md</a></strong>
</p>

<p align="center">
  <b>SmartBench never modifies your project's code.</b> It only analyzes, diagnoses, and recommends — you stay in full control.
</p>

---

## Table of Contents

- [Overview](#overview)
- [Demo Video](#demo-video)
- [The Problem SmartBench Solves](#the-problem-smartbench-solves)
- [5-Stage Diagnostic Pipeline](#5-stage-diagnostic-pipeline)
- [Supported Languages & Frameworks](#supported-languages--frameworks)
- [Multi-Agent Debate Architecture](#multi-agent-debate-architecture)
- [Code Graph + RAG Engine](#code-graph--rag-engine)
- [Diagnostic Tools (Pluggable)](#diagnostic-tools-pluggable)
- [Quick Start](#quick-start)
- [Configuration Guide](#configuration-guide)
- [Diagnosis Strategies](#diagnosis-strategies)
- [Extension Guide](#extension-guide)
- [FAQ](#faq)
- [Changelog Summary](#changelog-summary)
- [License](#license)

---

## Overview

SmartBench is an **LLM-powered universal code diagnostic platform** that subjects your codebase to a rigorous, multi-agent adversarial review. It catches bugs, performance bottlenecks, security vulnerabilities, architectural issues, and hotspots — all without hallucinating, because **every claim is backed by evidence**.

| Feature | Description |
|---|---|
| **Zero Hallucination** | Every diagnosis claim must cite exact file paths + line numbers, verified by disk I/O |
| **14 Languages** | Python, Go, Rust, C/C++, Java, Kotlin, JS/TS, Ruby, Swift, C#, Zig, and more |
| **20+ Frameworks** | FastAPI, Flask, Django, Gin, Echo, Express, NestJS, Next.js, React, Vue, Spring Boot, Axum, Actix, gRPC, and more |
| **Multi-Agent Debate** | Proposer -> Verifier -> Critique -> Cross-Verifier -> Judge |
| **Code Graph** | AST-based call graph + dependency graph across 12 languages |
| **RAG Vector Retrieval** | 3-tier embedding backend with dual vector store support |
| **Pluggable Tools** | GDB, Valgrind, pprof, py-spy, JFR, Arthas, and more |
| **8 LLM Providers** | Auto-detected from model name |
| **Safe by Design** | API keys in memory only, zero-disk persistence |

---

## Demo Video

[![SmartBench Demo](https://img.youtube.com/vi/your-demo-video-id/0.jpg)](https://www.youtube.com/watch?v=your-demo-video-id)

*Click the image above to watch the demo walkthrough.*

---

## The Problem SmartBench Solves

LLMs are powerful at understanding code, but they **hallucinate**. When you ask an LLM to review a codebase, it often invents non-existent bugs, references wrong files, or makes vague recommendations without evidence.

SmartBench solves this through a **multi-agent adversarial debate** where:

1. One agent **proposes** a diagnosis with hard evidence (file + line number)
2. A zero-LLM verifier **checks the evidence actually exists** on disk
3. Another agent **critiques** the proposal, trying to break it
4. A cross-verifier **re-checks** all evidence from the critique
5. A judge **renders a final verdict** based on the structured debate transcript

The result: **diagnoses you can trust**, backed by real code, real line numbers, and real tool output.

---

## 5-Stage Diagnostic Pipeline

```
┌─────────────────────────────────────────────────────────────────────────┐
│                    SmartBench 5-Stage Diagnostic Pipeline               │
└─────────────────────────────────────────────────────────────────────────┘

  ┌──────────────┐
  │  Stage 1     │
  │  Project     │  Zero-LLM fingerprinting: detect language, framework,
  │  Fingerprint │  build system, project structure, test framework
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  Stage 2     │
  │  LLM Project │  Optional: LLM reads project README, docs, config to
  │  Understand  │  build high-level understanding (skipped if no docs)
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  Stage 3     │
  │  Strategy    │  Select diagnosis strategy: performance, correctness,
  │  Selection   │  security, architecture, or hotspot analysis
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  Stage 4     │
  │  Code Graph  │  Build AST-based call graph + dependency graph.
  │  + RAG Index │  Vector-index key files via 3-tier embedding backend.
  └──────┬───────┘
         │
         ▼
  ┌──────────────┐
  │  Stage 5     │
  │  Multi-Agent │  Proposer -> Verifier -> Critique -> Cross-Verifier
  │  Debate +    │  -> Judge. Every claim evidence-verified via disk I/O.
  │  Evidence    │
  └──────────────┘
```

### Stage Details

**Stage 1 — Project Fingerprinting (Zero LLM)**
- Language detection from file extensions, shebangs, and config files
- Framework detection via dependency manifests (pyproject.toml, Cargo.toml, package.json, go.mod, etc.)
- Build system identification (Makefile, CMake, Gradle, Maven, etc.)
- Test framework discovery (pytest, jest, go test, cargo test, etc.)
- All detection is rule-based — no LLM calls, no cost, no latency

**Stage 2 — LLM Project Understanding (Optional)**
- Feeds project README, docs, and configuration to an LLM for high-level summarization
- Skipped entirely when no documentation is found — zero unnecessary cost
- Produces a concise "project context" used by all downstream agents

**Stage 3 — Diagnosis Strategy Selection**
- Five built-in strategy templates (see [Diagnosis Strategies](#diagnosis-strategies))
- Each strategy defines what the debate agents focus on
- Auto-selected based on project type, or user can override

**Stage 4 — Code Graph + RAG Indexing**
- AST-based call graph construction for 12 languages
- Dependency graph extraction
- Vector-embedding of key source files
- 3-tier embedding backend (see [Code Graph + RAG Engine](#code-graph--rag-engine))

**Stage 5 — Multi-Agent Debate + Evidence Verification**
- Full adversarial debate between multiple LLM agents
- Every factual claim verified against filesystem (no LLM involved)
- Structured output: diagnosis, severity, location, evidence, recommendation

---

## Supported Languages & Frameworks

### Languages

| Language | Status | File Extensions | AST Call Graph |
|---|---|---|---|
| Python | Full | `.py` | Yes |
| Go | Full | `.go` | Yes |
| Rust | Full | `.rs` | Yes |
| C | Full | `.c`, `.h` | Yes |
| C++ | Full | `.cpp`, `.hpp`, `.cc`, `.cxx` | Yes |
| Java | Full | `.java` | Yes |
| Kotlin | Full | `.kt`, `.kts` | Yes |
| JavaScript | Full | `.js`, `.mjs` | Yes |
| TypeScript | Full | `.ts`, `.tsx` | Yes |
| Ruby | Full | `.rb` | Yes |
| Swift | Full | `.swift` | Yes |
| C# | Full | `.cs` | Yes |
| Zig | Full | `.zig` | Yes |
| More | Extensible | Via config | Via plugins |

### Frameworks (20+ detected)

| Ecosystem | Frameworks |
|---|---|
| **Python** | FastAPI, Flask, Django, SQLAlchemy, Pydantic, Celery, asyncio |
| **Go** | Gin, Echo, Fiber, net/http, gRPC, Go kit |
| **Rust** | Axum, Actix-web, Tokio, Tower, Tonic, Serde |
| **JavaScript/TypeScript** | Express, NestJS, Next.js, React, Vue, Angular, Svelte, Fastify, Koa |
| **Java/Kotlin** | Spring Boot, Spring MVC, Micronaut, Quarkus, Javalin, Ktor |
| **C/C++** | gRPC, Boost, Qt, POCO, nlohmann/json, fmtlib |
| **Ruby** | Ruby on Rails, Sinatra, Grape |
| **C#** | ASP.NET Core, Entity Framework, SignalR, Blazor |
| **Swift** | Vapor, Kitura, SwiftNIO |

---

## Multi-Agent Debate Architecture

```
┌──────────────────────────────────────────────────────────────────┐
│                    Multi-Agent Debate Engine                     │
│                                                                  │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐                  │
│   │ Proposer │───▶│ Verifier │───▶│ Critique │                  │
│   │ (LLM)    │    │(Zero LLM)│    │ (LLM)    │                  │
│   └──────────┘    └──────────┘    └──────────┘                  │
│        │               │               │                        │
│        │   Evidence    │   Evidence    │   Evidence              │
│        │   Claims      │   Verified    │   Claims                │
│        ▼               ▼               ▼                        │
│   ┌──────────────────────────────────────────────────┐          │
│   │              Evidence Verification Layer           │          │
│   │    (Pure Disk I/O: file exists? line matches?)     │          │
│   └──────────────────────────────────────────────────┘          │
│        │               │               │                        │
│        ▼               ▼               ▼                        │
│   ┌──────────┐    ┌──────────┐    ┌──────────┐                  │
│   │ Verifier │    │   Judge  │    │  Result  │                  │
│   │(X-Check) │───▶│  (LLM)   │───▶│  Output  │                  │
│   │(Zero LLM)│    └──────────┘    └──────────┘                  │
│   └──────────┘                                                 │
└──────────────────────────────────────────────────────────────────┘
```

### Agent Roles

| Agent | Type | Responsibility |
|---|---|---|
| **Proposer** | LLM | Analyzes the codebase and proposes issues with exact file paths, line numbers, and code snippets as evidence |
| **Verifier (1st)** | Zero LLM | Reads the claimed files from disk, verifies lines exist and match, flags hallucinations immediately |
| **Critique** | LLM | Adversarially reviews the verified proposal — tries to find counterexamples, missing context, or false positives |
| **Verifier (2nd)** | Zero LLM | Independently verifies all evidence claims from the critique against disk |
| **Judge** | LLM | Reviews the complete debate transcript (proposal + verification + critique + cross-verification) and renders a final structured verdict |

### Evidence Verification Protocol

Every claim made by any LLM agent must follow this structure:

```json
{
  "diagnosis": "Potential buffer overflow in network packet parser",
  "severity": "critical",
  "evidence_claims": [
    {
      "file": "src/network/packet.c",
      "line": 142,
      "snippet": "memcpy(buffer, packet->data, packet->size);",
      "reasoning": "packet->size is read directly from network without bounds checking"
    }
  ],
  "recommendation": "Add bounds check before memcpy: if (packet->size > MAX_PACKET_SIZE)"
}
```

The **Verifier** agents (zero LLM) then:
1. Open the file from disk
2. Confirm the line number exists and the snippet matches
3. If verification fails → the claim is **rejected** with the actual file content shown
4. Only verified claims reach the Judge

This eliminates the single biggest problem in LLM code review: **hallucinated bugs**.

---

## Code Graph + RAG Engine

```
┌─────────────────────────────────────────────────────────────────┐
│                    Code Graph + RAG Architecture                │
└─────────────────────────────────────────────────────────────────┘

  ┌──────────────┐
  │  Source Code │
  │  (Project)   │
  └──────┬───────┘
         │
         ├──────────────────────┐
         ▼                      ▼
  ┌──────────────┐    ┌───────────────────┐
  │ AST Parser   │    │ Document Splitting│
  │ (12 langs)   │    │ (Hierarchical)    │
  └──────┬───────┘    └────────┬──────────┘
         │                     │
         ▼                     ▼
  ┌──────────────┐    ┌───────────────────┐
  │ Call Graph   │    │  Embedding Engine │
  │ Dependency   │    │  (3-Tier)         │
  │ Graph        │    │                   │
  └──────┬───────┘    │ ┌─────────────┐   │
         │            │ │ Tier 1:     │   │
         ▼            │ │ sentence-   │   │
  ┌──────────────┐    │ │ transformers │   │
  │ Graph Store  │    │ ├─────────────┤   │
  │ (NetworkX)   │    │ │ Tier 2:     │   │
  └──────────────┘    │ │ sklearn     │   │
         │            │ │ TF-IDF      │   │
         │            │ ├─────────────┤   │
         │            │ │ Tier 3:     │   │
         │            │ │ Character   │   │
         │            │ │ Hash        │   │
         │            │ └─────────────┘   │
         │            └────────┬──────────┘
         │                     │
         ▼                     ▼
  ┌──────────────┐    ┌───────────────────┐
  │ Graph Query  │    │  Vector Store     │
  │ (BFS/DFS,    │    │  ┌─────────────┐  │
  │  neighbors)  │    │  │ Default:    │  │
  └──────┬───────┘    │  │ SimpleVector │  │
         │            │  │ Store       │  │
         │            │  ├─────────────┤  │
         │            │  │ Optional:   │  │
         │            │  │ ChromaDB    │  │
         │            │  └─────────────┘  │
         │            └───────────────────┘
         │                     │
         └──────────┬──────────┘
                    ▼
          ┌──────────────────┐
          │  Context Builder │
          │  (Graph + RAG)   │
          └────────┬─────────┘
                   │
                   ▼
          ┌──────────────────┐
          │  Debate Agents   │
          └──────────────────┘
```

### AST Call Graph (12 Languages)

The code graph engine parses source files into ASTs and extracts:

- **Function/method call graphs** — who calls whom
- **Dependency graphs** — import/module relationships
- **Class hierarchy** — inheritance and interface implementations
- **Data flow edges** — variable definitions and usages

Built on top of `tree-sitter` for maximum language coverage and correctness.

### 3-Tier Embedding Backend

| Tier | Backend | When Used | Fallback Trigger |
|---|---|---|---|
| 1 | `sentence-transformers` | GPU/CPU available, quality preferred | Import error or OOM |
| 2 | `sklearn` TF-IDF | No GPU, limited RAM | Import error |
| 3 | Character Hash | Always works | Final fallback — guaranteed |

The system auto-downgrades tiers gracefully. You never need to configure it.

### Vector Stores

| Store | Default? | Description |
|---|---|---|
| **SimpleVectorStore** | Yes | Pure Python, no dependencies, fast for <100K docs |
| **ChromaDB** | Optional | Persistent, scalable, suitable for large projects |

---

## Diagnostic Tools (Pluggable)

SmartBench integrates system-level and language-specific diagnostic tools. Each tool is wrapped in a uniform interface and invoked during the diagnostic pipeline.

| Category | Tool | Platform | Description |
|---|---|---|---|
| **System** | `dmesg` | Linux | Kernel log for OOM, segfaults, hardware errors |
| **System** | `ps` | Linux/macOS | Process listing, CPU/memory per process |
| **System** | `vmstat` | Linux | System memory, paging, swap, I/O stats |
| **System** | `top` | Linux/macOS | Real-time process resource usage |
| **System** | `iostat` | Linux | I/O statistics per device |
| **System** | `lsof` | Linux/macOS | Open file descriptors per process |
| **Go** | `pprof` | Go projects | CPU, memory, goroutine, mutex profiling |
| **Go** | `race detector` | Go projects | Data race detection at runtime |
| **Go** | `trace` | Go projects | goroutine scheduling and execution traces |
| **Python** | `tracemalloc` | Python projects | Memory allocation trace with stack traces |
| **Python** | `py-spy` | Python projects | Sampling profiler, no code modification |
| **Python** | `cProfile` | Python projects | Deterministic function-level profiling |
| **Python** | `memray` | Python projects | Memory profiler with native allocations |
| **C/C++** | `GDB` | C/C++ projects | Debugger for crash analysis, backtrace |
| **C/C++** | `Valgrind` | C/C++ projects | Memory leak, invalid access, undefined behavior |
| **C/C++** | `ASAN` | C/C++ projects | Address Sanitizer for runtime memory errors |
| **C/C++** | `perf` | C/C++ projects (Linux) | CPU sampling, cache misses, branch prediction |
| **C/C++** | `strace` | C/C++ projects (Linux) | System call tracing |
| **Java** | `JFR` | Java projects | JDK Flight Recorder — low-overhead profiling |
| **Java** | `jstack` | Java projects | Thread dump for deadlock analysis |
| **Java** | `jmap` | Java projects | Heap dump analysis |
| **Java** | `Arthas` | Java projects | Alibaba's real-time diagnostic tool |
| **Java** | `Async Profiler` | Java projects | CPU and allocation profiling |

Tools are **auto-detected** based on the project language and availability on the system. You can also manually specify which tools to invoke.

---

## Quick Start

### Prerequisites

- **Python 3.10+**
- **pip** (preferably in a virtual environment)

### Installation

```bash
# Clone the repository
git clone https://github.com/xianyu-sheng/SmartBench.git
cd SmartBench

# Create virtual environment (recommended)
python -m venv venv
source venv/bin/activate  # On Windows: venv\Scripts\activate

# Install dependencies
pip install -r requirements.txt

# Optional: Install sentence-transformers for Tier 1 embeddings
pip install sentence-transformers

# Optional: Install ChromaDB for persistent vector storage
pip install chromadb
```

### Interactive Wizard Walkthrough

SmartBench features an interactive CLI wizard that guides you through configuration. Here's a typical session:

```bash
$ python -m smartbench --wizard

╔══════════════════════════════════════════════════════════════╗
║           SmartBench Configuration Wizard v0.6              ║
║       LLM-Powered Universal Code Diagnostic Platform        ║
╚══════════════════════════════════════════════════════════════╝

Step 1: LLM Provider
────────────────────────────────────────────────────────
Detected 8 available providers.
Select provider (openai/anthropic/google/deepseek/ollama/qwen/groq/mistral):
> deepseek

Model name (e.g., deepseek-chat, gpt-4o, claude-sonnet-4):
> deepseek-chat

API key (will be stored in memory only, never on disk):
> ********************************

Step 2: Project Target
────────────────────────────────────────────────────────
Enter the path to the project you want to diagnose:
> /home/user/projects/my-web-app

Step 3: Diagnosis Strategy
────────────────────────────────────────────────────────
Available strategies:
  1. performance_analysis
  2. correctness_audit
  3. architecture_review
  4. security_scan
  5. hotspot_analysis

Select strategy (1-5) or press Enter for auto-detect:
> 1

Step 4: Diagnostic Tools
────────────────────────────────────────────────────────
Auto-detected tools for this project (Python/FastAPI):
  [x] tracemalloc
  [x] py-spy
  [x] cProfile
  [ ] memray (not installed)

Enable all detected tools? (Y/n):
> Y

Step 5: RAG Configuration
────────────────────────────────────────────────────────
Embedding backend (auto/sentence-transformers/tfidf/char-hash):
> auto

Vector store (simple/chroma):
> simple

Step 6: Confirm & Run
────────────────────────────────────────────────────────

Summary:
  Project:    /home/user/projects/my-web-app
  Language:   Python
  Framework:  FastAPI
  Strategy:   performance_analysis
  Provider:   deepseek (deepseek-chat)
  Tools:      3 enabled
  RAG:        sentence-transformers > SimpleVectorStore

Proceed with diagnosis? (Y/n):
> Y

━━━━ Running SmartBench Diagnosis ────
[1/5] Project Fingerprinting...        ✅
[2/5] LLM Project Understanding...     ✅
[3/5] Strategy Selection...            ✅
[4/5] Code Graph + RAG Indexing...     ✅
[5/5] Multi-Agent Debate...            ████████░░ 68%

  Debate Round 1:
    Proposer:   Identified 3 potential performance issues
    Verifier:   All evidence claims verified ✅
    Critique:   1 issue contested with counter-example
    Cross-Verify: Critique evidence verified ✅
    ...

━━━━ Diagnosis Complete ────

Results written to: smartbench_report_20260628_153022/
├── summary.json
├── detailed_report.md
└── debate_transcript.json
```

### Running Without Wizard

```bash
# Basic usage with config file
python -m smartbench --project /path/to/project --config config/default.yaml

# All-in-one flags
python -m smartbench \
  --project /path/to/project \
  --provider deepseek \
  --model deepseek-chat \
  --api-key sk-xxxx \
  --strategy security_scan \
  --tools auto

# List supported strategies
python -m smartbench --list-strategies

# List supported languages
python -m smartbench --list-languages
```

---

## Configuration Guide

SmartBench supports two configuration modes:

### 1. Interactive CLI Wizard (Recommended)

Run `python -m smartbench --wizard` and follow the prompts. The wizard:
- Auto-detects your environment
- Walks through provider selection, project setup, and tool configuration
- Confirms before execution
- Stores API keys in memory only

### 2. YAML Config File (`config/default.yaml`)

For repeatable or automated runs, use the YAML config file:

```yaml
# config/default.yaml
project:
  path: "/path/to/project"
  language: auto     # auto or specific language
  framework: auto    # auto or specific framework

llm:
  provider: deepseek  # openai, anthropic, google, deepseek, ollama, qwen, groq, mistral
  model: deepseek-chat
  api_key_env: SMARTBENCH_API_KEY  # Read from environment variable
  temperature: 0.3

strategy:
  type: auto         # auto or specific strategy name

rag:
  embedding: auto    # auto, sentence-transformers, tfidf, char-hash
  vector_store: simple  # simple or chroma

tools:
  mode: auto         # auto, all, or manual list
  # manual list example:
  # include:
  #   - python: [tracemalloc, py-spy]
  #   - system: [dmesg, ps]

output:
  dir: "./smartbench_output"
  format: ["json", "markdown"]
```

### LLM Provider Auto-Detection

SmartBench automatically detects the LLM provider from the model name:

| Provider | Model Prefix Examples |
|---|---|
| **OpenAI** | `gpt-4o`, `gpt-4`, `gpt-3.5-turbo` |
| **Anthropic** | `claude-sonnet`, `claude-opus`, `claude-haiku` |
| **Google** | `gemini-pro`, `gemini-ultra` |
| **DeepSeek** | `deepseek-chat`, `deepseek-reasoner` |
| **Ollama** | Any local model via Ollama |
| **Qwen** | `qwen-plus`, `qwen-max`, `qwen-turbo` |
| **Groq** | `groq-mixtral`, `groq-llama` |
| **Mistral** | `mistral-large`, `mistral-small` |

---

## Diagnosis Strategies

SmartBench includes five verified strategy templates:

### 1. Performance Analysis (`performance_analysis`)
- Identifies slow code paths, tight loops, and algorithmic inefficiencies
- Detects N+1 queries, unnecessary allocations, and blocking I/O
- Suggests caching, batching, and concurrency improvements

### 2. Correctness Audit (`correctness_audit`)
- Finds race conditions, deadlocks, and synchronization bugs
- Detects improper error handling, resource leaks, and edge cases
- Validates input validation and type safety

### 3. Architecture Review (`architecture_review`)
- Analyzes module coupling, circular dependencies, and layer violations
- Evaluates adherence to SOLID principles and design patterns
- Reviews API design and interface segregation

### 4. Security Scan (`security_scan`)
- Detects injection vulnerabilities (SQL, command, XSS)
- Finds hardcoded secrets, insecure cryptographic usage, and permission issues
- Reviews input sanitization and output encoding

### 5. Hotspot Analysis (`hotspot_analysis`)
- Identifies files with high churn, complexity, or bug density
- Detects code duplication and excessively long functions
- Highlights areas most likely to benefit from refactoring

New strategies can be added via the extension system.

---

## Extension Guide

### Adding a New Language

1. Add the language to `config/languages.yaml`:
   ```yaml
   mylang:
     extensions: [".my"]
     comment_style: "//"
     frameworks: ["myframework"]
   ```

2. Implement AST parsing (optional, for call graph):
   - Create a parser in `smartbench/graph/parsers/mylang_parser.py`
   - Inherit from `BaseParser` and implement `extract_calls()` and `extract_deps()`

3. Add zero-LLM fingerprinting rules in:
   - `smartbench/fingerprint/languages.py` for language detection
   - `smartbench/fingerprint/frameworks.py` for framework detection

### Adding a New LLM Provider

1. Create a provider class in `smartbench/llm/providers/`:
   ```python
   from smartbench.llm.base import BaseLLMProvider

   class MyProvider(BaseLLMProvider):
       @property
       def name(self) -> str:
           return "myprovider"

       def chat(self, messages, **kwargs) -> str:
           # Implement chat completion
           pass
   ```

2. Register model prefixes in `smartbench/llm/discovery.py`:
   ```python
   PROVIDER_MAP = {
       "myprovider-": "myprovider",
       # ...
   }
   ```

3. That's it. SmartBench will auto-detect your provider from model names.

### Adding a Diagnostic Tool

1. Create a tool wrapper in `smartbench/tools/`:
   ```python
   from smartbench.tools.base import BaseTool

   class MyTool(BaseTool):
       name = "my_tool"
       languages = ["python", "go"]  # Supported languages

       def is_available(self) -> bool:
           # Check if tool is installed on the system
           pass

       def run(self, project_path: str) -> dict:
           # Execute tool and return structured results
           pass
   ```

2. Register the tool in `smartbench/tools/registry.py`.

---

## FAQ

### Q: Does SmartBench modify my code?
**No.** SmartBench is a read-only diagnostic platform. It never writes to any file in the target project. All output is written to a separate report directory.

### Q: How does SmartBench prevent hallucinations?
SmartBench uses a two-layer defense:
1. **Evidence Verification**: Every claim made by an LLM agent must include exact file paths and line numbers. A zero-LLM verifier reads those files from disk and confirms the evidence is real.
2. **Multi-Agent Adversarial Debate**: The Critique agent actively tries to disprove proposals, and the Judge evaluates the full debate transcript.

### Q: Do I need a GPU?
No. The embedding engine auto-downgrades from `sentence-transformers` (which benefits from GPU) to TF-IDF to character hash — all CPU-compatible. The LLM calls go to remote APIs or your local Ollama instance.

### Q: Where are my API keys stored?
**In memory only.** API keys are accepted via CLI flags, environment variables, or the interactive wizard. They are never written to disk, logs, or config files. When the process exits, the keys are gone.

### Q: How much does it cost to run?
This depends entirely on the LLM provider you choose:
- **Local (Ollama)**: Free, runs on your machine
- **DeepSeek / Groq**: Typically $0.01-0.10 per diagnosis
- **OpenAI / Anthropic**: Typically $0.05-0.50 per diagnosis

Cost scales with project size. The zero-LLM fingerprinting and evidence verification stages cost nothing.

### Q: Can I use it in CI/CD?
Yes. SmartBench can run non-interactively with a YAML config file or command-line flags. Output is written as structured JSON, suitable for ingestion by CI systems.

### Q: What if my framework isn't supported?
SmartBench's fingerprinting is rule-based and extensible. You can add framework detection rules in the config file. If the project language is detected, the analysis still works — it just won't have framework-specific context.

---

## Changelog Summary

### v0.6 — Universal Platform + RAG + Evidence Verification
- **Rebranded**: From Raft KV store to Universal Code Diagnostic Platform
- **RAG Vector Retrieval**: 3-tier embedding backend (sentence-transformers -> TF-IDF -> char hash)
- **Dual Vector Store**: SimpleVectorStore (default) + ChromaDB (optional)
- **Evidence Verification**: Zero-LLM verifier agents that check claims against disk I/O
- **14 Language Support**: AST-based call graph engine
- **20+ Framework Detection**: Language-agnostic fingerprinting system
- **5 Strategy Templates**: performance, correctness, architecture, security, hotspot
- **Pluggable Diagnostic Tools**: System-level + language-specific profiling tools
- **8 LLM Providers**: With auto-detection from model name
- **Interactive CLI Wizard**: Step-by-step configuration
- **Memory-Only API Keys**: Zero disk persistence for credentials

### v0.1 — Raft KV Store (Initial)
- Distributed key-value store based on the Raft consensus algorithm
- Leader election and log replication
- Basic HTTP API for get/set/delete operations
- Single-server and cluster modes

---

## License

MIT License

Copyright (c) 2025-2026 Xianyu Sheng

Permission is hereby granted, free of charge, to any person obtaining a copy
of this software and associated documentation files (the "Software"), to deal
in the Software without restriction, including without limitation the rights
to use, copy, modify, merge, publish, distribute, sublicense, and/or sell
copies of the Software, and to permit persons to whom the Software is
furnished to do so, subject to the following conditions:

The above copyright notice and this permission notice shall be included in all
copies or substantial portions of the Software.

THE SOFTWARE IS PROVIDED "AS IS", WITHOUT WARRANTY OF ANY KIND, EXPRESS OR
IMPLIED, INCLUDING BUT NOT LIMITED TO THE WARRANTIES OF MERCHANTABILITY,
FITNESS FOR A PARTICULAR PURPOSE AND NONINFRINGEMENT. IN NO EVENT SHALL THE
AUTHORS OR COPYRIGHT HOLDERS BE LIABLE FOR ANY CLAIM, DAMAGES OR OTHER
LIABILITY, WHETHER IN AN ACTION OF CONTRACT, TORT OR OTHERWISE, ARISING FROM,
OUT OF OR IN CONNECTION WITH THE SOFTWARE OR THE USE OR OTHER DEALINGS IN THE
SOFTWARE.

---

<p align="center">
  <a href="https://github.com/xianyu-sheng/SmartBench">GitHub</a> ·
  <a href="README_CN.md">中文文档</a> ·
  <a href="https://github.com/xianyu-sheng/SmartBench/issues">Issues</a> ·
  <a href="https://github.com/xianyu-sheng/SmartBench/discussions">Discussions</a>
</p>

<p align="center">
  <sub>Built with ❤️ by <a href="https://github.com/xianyu-sheng">Xianyu Sheng</a></sub>
</p>
