"""
Concrete diagnostic tool implementations.

Each tool inherits from DiagnosticTool and declares:
- applicable_languages: which languages it can diagnose
- applicable_categories: which problem types it handles
- diagnose(): how to run the tool and parse its output
"""

import ast
import os
import re
import shlex
from pathlib import Path
from typing import Dict, List, Optional

from smartbench.detector.fingerprint import Language
from smartbench.diagnostics.registry import (
    DiagnosisResult,
    DiagnosticTool,
    ProblemCategory,
    Severity,
)
from smartbench.path_safety import is_project_file

_PYTHON_SCAN_EXCLUDED_DIRS = {
    ".git",
    ".mypy_cache",
    ".pytest_cache",
    ".smartbench",
    ".tox",
    ".venv",
    "__pycache__",
    "build",
    "dist",
    "node_modules",
    "target",
    "vendor",
    "venv",
}
_PYTHON_SCAN_MAX_DIRECTORIES = 2_000
_PYTHON_SCAN_MAX_DISCOVERED_FILES = 20_000
_PYTHON_SCAN_MAX_FILES = 500
_PYTHON_SCAN_MAX_FILE_BYTES = 2 * 1024 * 1024
_PYTHON_ENTRY_POINTS = (
    "main.py",
    "app.py",
    "server.py",
    "cli.py",
    "start.py",
    "run.py",
    "__main__.py",
)

# ── Linux / Unix system tools ─────────────────────────────────────────

class DMesgTool(DiagnosticTool):
    """Kernel log analysis — crashes, OOM, hardware issues."""

    name = "dmesg"
    requires_system_access = True
    applicable_languages = list(Language)
    applicable_categories = [
        ProblemCategory.CRASH, ProblemCategory.STARTUP_FAILURE,
    ]

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        result = self._run_command(["dmesg"], timeout=10)
        evidence = "\n".join(result.stdout.splitlines()[-100:])[-3000:]
        output = result.stdout + result.stderr

        if result.returncode != 0:
            return DiagnosisResult(
                tool_name=self.name,
                problem_category=category,
                evidence=output[-3000:],
                success=False,
                error=result.stderr.strip() or "dmesg failed",
            )

        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
            evidence=evidence,
        )

        patterns = {
            ProblemCategory.CRASH: [
                (r"segfault", "Segmentation fault detected"),
                (r"SIGSEGV", "SIGSEGV signal — invalid memory access"),
                (r"SIGABRT", "SIGABRT — process aborted"),
                (r"kernel panic", "Kernel panic"),
                (r"Oops", "Kernel Oops"),
            ],
            ProblemCategory.STARTUP_FAILURE: [
                (r"failed to start", "Service failed to start"),
                (r"cannot open", "Cannot open file/resource"),
                (r"permission denied", "Permission denied"),
                (r"not found", "Required file not found"),
            ],
        }

        for pattern, description in patterns.get(category, []):
            if re.search(pattern, output, re.IGNORECASE):
                findings.symptoms.append(description)

        if findings.symptoms:
            findings.confidence = 0.7
            findings.severity = Severity.HIGH

        return findings


class ProcessTool(DiagnosticTool):
    """Process listing — deadlocks, resource usage."""

    name = "ps"
    requires_system_access = True
    applicable_languages = list(Language)
    applicable_categories = [
        ProblemCategory.DEADLOCK, ProblemCategory.PERFORMANCE,
        ProblemCategory.MEMORY_LEAK,
    ]

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        result = self._run_command(["ps", "aux", "--sort=-%mem"], timeout=10)
        evidence = "\n".join(result.stdout.splitlines()[:20])[:2000]

        if result.returncode != 0:
            return DiagnosisResult(
                tool_name=self.name,
                problem_category=category,
                evidence=(result.stdout + result.stderr)[-2000:],
                success=False,
                error=result.stderr.strip() or "ps failed",
            )

        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
            evidence=evidence,
        )

        if category == ProblemCategory.DEADLOCK:
            # Check for D-state (uninterruptible sleep) processes
            d_state = re.findall(r"\sD\s", evidence)
            if d_state:
                findings.symptoms.append(f"{len(d_state)} processes in D-state (possible deadlock)")
                findings.severity = Severity.HIGH
                findings.confidence = 0.5

        elif category == ProblemCategory.MEMORY_LEAK:
            # Check for high memory usage
            for line in evidence.split("\n"):
                parts = line.split()
                if len(parts) >= 4:
                    try:
                        mem = float(parts[3])
                        if mem > 50:
                            findings.symptoms.append(
                                f"Process {parts[10] if len(parts) > 10 else 'unknown'} "
                                f"using {mem}% memory"
                            )
                    except (ValueError, IndexError):
                        pass

        return findings


class VMStatTool(DiagnosticTool):
    """Virtual memory statistics — page faults, swapping."""

    name = "vmstat"
    requires_system_access = True
    applicable_languages = list(Language)
    applicable_categories = [ProblemCategory.PERFORMANCE, ProblemCategory.MEMORY_LEAK]

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        result = self._run_command(["vmstat", "1", "3"], timeout=10)
        evidence = result.stdout[:2000]

        if result.returncode != 0:
            return DiagnosisResult(
                tool_name=self.name,
                problem_category=category,
                evidence=(result.stdout + result.stderr)[-2000:],
                success=False,
                error=result.stderr.strip() or "vmstat failed",
            )

        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
            evidence=evidence,
        )

        # Parse si/so columns (swap in/out)
        for line in evidence.split("\n")[2:]:
            parts = line.split()
            if len(parts) >= 8:
                try:
                    si = int(parts[6])
                    so = int(parts[7])
                    if si > 0 or so > 0:
                        findings.symptoms.append("Swap activity detected — possible memory pressure")
                        findings.confidence = 0.6
                        break
                except (ValueError, IndexError):
                    continue

        return findings


# ── Go-specific tools ─────────────────────────────────────────────────

class GoPProfTool(DiagnosticTool):
    """Go pprof — CPU, memory, goroutine profiling."""

    name = "go"
    applicable_languages = [Language.GO]
    applicable_categories = [
        ProblemCategory.PERFORMANCE, ProblemCategory.MEMORY_LEAK,
        ProblemCategory.DEADLOCK, ProblemCategory.CONCURRENCY,
    ]

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
        )

        # Check if go toolchain is available
        go_check = self._run_command(["go", "version"], timeout=5)
        if go_check.returncode != 0:
            findings.success = False
            findings.error = "Go toolchain not found"
            return findings

        if category == ProblemCategory.CONCURRENCY:
            # Race detector
            result = self._run_command(
                ["go", "test", "-race", "./..."],
                timeout=120,
                cwd=target_path,
            )
            output = result.stdout + result.stderr
            findings.evidence = "\n".join(output.splitlines()[:50])[:3000]
            findings.commands_used = ["go test -race ./..."]
            if "WARNING: DATA RACE" in output:
                findings.symptoms.append("Data race detected")
                findings.severity = Severity.CRITICAL
                findings.confidence = 0.95
            elif result.returncode != 0:
                findings.success = False
                findings.error = result.stderr.strip() or "go test -race failed"

        elif category == ProblemCategory.PERFORMANCE:
            # Build and suggest pprof endpoints
            result = self._run_command(
                ["go", "build", "./..."],
                timeout=60,
                cwd=target_path,
            )
            output = result.stdout + result.stderr
            findings.evidence = "\n".join(output.splitlines()[:20])[:2000] or "Build OK"
            findings.commands_used = ["go build ./..."]
            if result.returncode != 0:
                findings.success = False
                findings.error = result.stderr.strip() or "go build failed"
            findings.suggestions.append({
                "title": "Run pprof CPU profile",
                "command": "go tool pprof -http=:8080 http://localhost:6060/debug/pprof/profile?seconds=30",
                "description": "Start CPU profiling for 30 seconds and open web UI",
            })
            findings.suggestions.append({
                "title": "Check goroutine count",
                "command": "go tool pprof http://localhost:6060/debug/pprof/goroutine",
                "description": "Profile goroutine stacks to detect leaks",
            })

        elif category == ProblemCategory.MEMORY_LEAK:
            findings.suggestions.append({
                "title": "Run heap profile",
                "command": "go tool pprof -http=:8080 http://localhost:6060/debug/pprof/heap",
                "description": "Profile heap allocations to find memory leaks",
            })

        return findings


# ── Python-specific tools ─────────────────────────────────────────────

class PythonDiagTool(DiagnosticTool):
    """Python diagnostic tools — tracemalloc, py-spy, pytest."""

    name = "python"
    applicable_languages = [Language.PYTHON]
    applicable_categories = [
        ProblemCategory.PERFORMANCE, ProblemCategory.MEMORY_LEAK,
        ProblemCategory.STARTUP_FAILURE, ProblemCategory.DEPENDENCY,
    ]

    def is_available(self) -> bool:
        """Always available — gives suggestions even without specific tools."""
        return True

    @staticmethod
    def _discover_python_files(target_path: str) -> List[Path]:
        """Return a bounded list of project-owned Python files."""
        supplied = Path(target_path)
        try:
            if supplied.is_symlink():
                return []
            root = supplied.resolve(strict=True)
        except (OSError, RuntimeError):
            return []

        if root.is_file():
            try:
                if (
                    root.suffix.lower() == ".py"
                    and root.stat().st_size <= _PYTHON_SCAN_MAX_FILE_BYTES
                ):
                    return [root]
            except OSError:
                pass
            return []
        if not root.is_dir():
            return []

        paths: List[Path] = []
        visited_directories = 0
        discovered_files = 0
        for current, dirnames, filenames in os.walk(
            root, topdown=True, followlinks=False
        ):
            visited_directories += 1
            if visited_directories > _PYTHON_SCAN_MAX_DIRECTORIES:
                break

            current_path = Path(current)
            safe_dirs = []
            for dirname in sorted(dirnames):
                candidate = current_path / dirname
                if (
                    dirname in _PYTHON_SCAN_EXCLUDED_DIRS
                    or candidate.is_symlink()
                ):
                    continue
                try:
                    candidate.resolve().relative_to(root)
                except (OSError, RuntimeError, ValueError):
                    continue
                safe_dirs.append(dirname)
            dirnames[:] = safe_dirs

            for filename in sorted(filenames):
                discovered_files += 1
                if discovered_files > _PYTHON_SCAN_MAX_DISCOVERED_FILES:
                    return paths
                if not filename.lower().endswith(".py"):
                    continue
                path = current_path / filename
                if not is_project_file(root, path):
                    continue
                try:
                    if path.stat().st_size > _PYTHON_SCAN_MAX_FILE_BYTES:
                        continue
                except OSError:
                    continue
                paths.append(path)
                if len(paths) >= _PYTHON_SCAN_MAX_FILES:
                    return paths
        return paths

    @classmethod
    def _profile_target(cls, target_path: str) -> tuple[str, bool]:
        """Choose a runnable Python file, or return an explicit placeholder."""
        supplied = Path(target_path)
        try:
            if supplied.is_symlink():
                raise ValueError("symlink targets are not inferred")
            resolved = supplied.resolve(strict=True)
        except (OSError, RuntimeError, ValueError):
            return "path/to/entry_script.py", True

        if resolved.is_file():
            if resolved.suffix.lower() == ".py":
                return shlex.quote(str(resolved)), False
            return "path/to/entry_script.py", True

        if resolved.is_dir():
            for name in _PYTHON_ENTRY_POINTS:
                candidate = resolved / name
                if is_project_file(resolved, candidate):
                    return shlex.quote(str(candidate)), False

            for candidate in cls._discover_python_files(str(resolved)):
                if candidate.name == "__main__.py":
                    return shlex.quote(str(candidate)), False

        return "path/to/entry_script.py", True

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
        )

        if category == ProblemCategory.STARTUP_FAILURE:
            root = Path(target_path).resolve()
            checked = 0
            syntax_errors = []
            for path in self._discover_python_files(target_path):
                try:
                    ast.parse(
                        path.read_text(encoding="utf-8", errors="ignore"),
                        filename=(
                            str(path.relative_to(root))
                            if root.is_dir()
                            else path.name
                        ),
                    )
                    checked += 1
                except SyntaxError as exc:
                    try:
                        display_path = (
                            path.relative_to(root) if root.is_dir() else path.name
                        )
                    except ValueError:
                        display_path = path.name
                    syntax_errors.append(
                        f"{display_path}:{exc.lineno}: {exc.msg}"
                    )
                except OSError:
                    continue
            findings.evidence = (
                "\n".join(syntax_errors)
                if syntax_errors else f"Parsed {checked} Python files without syntax errors"
            )
            if syntax_errors:
                findings.symptoms.append(
                    f"{len(syntax_errors)} Python syntax error(s) detected"
                )
                findings.severity = Severity.HIGH
                findings.confidence = 1.0

        elif category == ProblemCategory.DEPENDENCY:
            findings.evidence = (
                "Dependency consistency was not executed automatically because "
                "SmartBench cannot safely infer the target environment."
            )
            findings.suggestions.append({
                "title": "Check the target Python environment",
                "command": "python -m pip check",
                "description": "Run inside the project's activated virtual environment",
            })

        elif category == ProblemCategory.PERFORMANCE:
            profile_target, is_placeholder = self._profile_target(target_path)
            target_note = (
                "Replace path/to/entry_script.py with the real Python entry point"
                if is_placeholder
                else "The Python entry point was inferred from the diagnostic target"
            )
            findings.suggestions.append({
                "title": "Profile with py-spy",
                "command": (
                    "py-spy record -o profile.svg -- python "
                    f"{profile_target}"
                ),
                "description": (
                    "Generate a flame graph with py-spy (pip install py-spy). "
                    f"{target_note}."
                ),
            })
            findings.suggestions.append({
                "title": "Profile with cProfile",
                "command": (
                    "python -m cProfile -o profile.out "
                    f"{profile_target}"
                ),
                "description": (
                    "Use built-in cProfile for function-level profiling. "
                    f"{target_note}."
                ),
            })

        elif category == ProblemCategory.MEMORY_LEAK:
            findings.suggestions.append({
                "title": "Use tracemalloc",
                "command": "python -X tracemalloc=10 your_script.py",
                "description": "Enable tracemalloc to track memory allocations",
            })

        findings.success = True
        return findings


# ── C/C++ tools ───────────────────────────────────────────────────────

class CPPDiagTool(DiagnosticTool):
    """C/C++ diagnostic tools — gdb, valgrind, ASAN hints."""

    name = "cpp"
    applicable_languages = [Language.CPP, Language.C]
    applicable_categories = [
        ProblemCategory.CRASH, ProblemCategory.MEMORY_LEAK,
        ProblemCategory.PERFORMANCE,
    ]

    def is_available(self) -> bool:
        """Always available — gives suggestions even without specific tools."""
        return True

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
        )

        if category == ProblemCategory.CRASH:
            # GDB check
            gdb_check = self._run_command(["gdb", "--version"], timeout=5)
            if gdb_check.returncode == 0:
                findings.suggestions.append({
                    "title": "Analyze core dump with GDB",
                    "command": f"gdb -batch -ex 'bt full' -ex 'quit' {target_path} core",
                    "description": "Get full backtrace from core dump",
                })
            else:
                findings.suggestions.append({
                    "title": "Install GDB",
                    "command": "apt-get install gdb  # or brew install gdb",
                    "description": "GDB is required for crash analysis",
                })

            # Check for ASAN
            findings.suggestions.append({
                "title": "Build with Address Sanitizer",
                "command": "g++ -fsanitize=address -g -O1 your_code.cpp",
                "description": "ASAN detects use-after-free, buffer overflow, leaks",
            })

        elif category == ProblemCategory.MEMORY_LEAK:
            valgrind_check = self._run_command(["valgrind", "--version"], timeout=5)
            if valgrind_check.returncode == 0:
                findings.suggestions.append({
                    "title": "Run Valgrind",
                    "command": f"valgrind --leak-check=full --show-leak-kinds=all {target_path}",
                    "description": "Full memory leak analysis with Valgrind",
                })

        elif category == ProblemCategory.PERFORMANCE:
            perf_check = self._run_command(["perf", "--version"], timeout=5)
            if perf_check.returncode == 0:
                findings.suggestions.append({
                    "title": "CPU profiling with perf",
                    "command": f"perf record -F 99 -g -- {target_path} && perf script | stackcollapse-perf.pl | flamegraph.pl > flamegraph.svg",
                    "description": "Generate flame graph with perf + FlameGraph scripts",
                })

        findings.success = True
        return findings


# ── Java/JVM tools ────────────────────────────────────────────────────

class JavaDiagTool(DiagnosticTool):
    """Java diagnostic tools — jstack, jmap, Arthas hints."""

    name = "java"
    applicable_languages = [Language.JAVA, Language.KOTLIN]
    applicable_categories = [
        ProblemCategory.DEADLOCK, ProblemCategory.MEMORY_LEAK,
        ProblemCategory.PERFORMANCE,
    ]

    def is_available(self) -> bool:
        """Always available — gives suggestions even without specific tools."""
        return True

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        findings = DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
        )

        if category == ProblemCategory.DEADLOCK:
            findings.suggestions.append({
                "title": "Detect deadlocks with jstack",
                "command": "jstack <pid> | grep -A 10 'deadlock'",
                "description": "Find deadlocked threads in JVM thread dump",
            })

        elif category == ProblemCategory.MEMORY_LEAK:
            findings.suggestions.append({
                "title": "Heap dump analysis",
                "command": "jmap -dump:live,format=b,file=heap.hprof <pid>",
                "description": "Generate heap dump for analysis with MAT / VisualVM",
            })

        elif category == ProblemCategory.PERFORMANCE:
            findings.suggestions.append({
                "title": "JFR recording",
                "command": "jcmd <pid> JFR.start duration=60s filename=recording.jfr",
                "description": "Java Flight Recorder — low-overhead profiling",
            })

        findings.success = True
        return findings


# ── Static analysis tools ─────────────────────────────────────────────

class StaticAnalysisTool(DiagnosticTool):
    """Generic static analysis suggestions per language."""

    name = "static_analysis"
    applicable_languages = list(Language)
    applicable_categories = [ProblemCategory.CODE_QUALITY, ProblemCategory.SECURITY]

    def is_available(self) -> bool:
        """Static analysis always available — returns suggestions, not binaries."""
        return True

    _SUGGESTIONS = {
        Language.PYTHON: [
            {"title": "Run ruff", "command": "pip install ruff && ruff check .",
             "description": "Fast Python linter and formatter"},
            {"title": "Run mypy", "command": "pip install mypy && mypy .",
             "description": "Static type checking"},
            {"title": "Run bandit", "command": "pip install bandit && bandit -r .",
             "description": "Security-focused static analysis"},
        ],
        Language.GO: [
            {"title": "Run go vet", "command": "go vet ./...",
             "description": "Go's built-in static analyzer"},
            {"title": "Run staticcheck", "command": "staticcheck ./...",
             "description": "Advanced Go static analysis (install: go install honnef.co/go/tools/cmd/staticcheck@latest)"},
            {"title": "Run golangci-lint", "command": "golangci-lint run ./...",
             "description": "Comprehensive Go linter aggregator"},
        ],
        Language.RUST: [
            {"title": "Run clippy", "command": "cargo clippy -- -D warnings",
             "description": "Rust's official linter"},
            {"title": "Run cargo audit", "command": "cargo audit",
             "description": "Check dependencies for security vulnerabilities"},
        ],
        Language.CPP: [
            {"title": "Run clang-tidy", "command": "clang-tidy *.cpp -- -std=c++17",
             "description": "Clang-based C++ linter"},
            {"title": "Run cppcheck", "command": "cppcheck --enable=all .",
             "description": "Static analysis for C/C++"},
        ],
    }

    def diagnose(self, target_path: str, category: ProblemCategory,
                 symptoms: Optional[List[str]] = None,
                 extra_args: Optional[Dict] = None) -> DiagnosisResult:
        # This tool always succeeds — it just gives suggestions
        lang = Language.PYTHON  # default
        if extra_args and "language" in extra_args:
            lang = extra_args["language"]

        languages = [lang]
        if extra_args and isinstance(extra_args.get("languages"), list):
            languages = extra_args["languages"]
        suggestions = []
        seen_commands = set()
        for candidate in languages:
            for suggestion in self._SUGGESTIONS.get(candidate, []):
                command = suggestion.get("command", "")
                if command in seen_commands:
                    continue
                seen_commands.add(command)
                suggestions.append(dict(suggestion))
        return DiagnosisResult(
            tool_name=self.name,
            problem_category=category,
            suggestions=suggestions,
            confidence=0.8,
            success=True,
        )


# ── Exported tool list ────────────────────────────────────────────────

ALL_TOOLS: List[DiagnosticTool] = [
    DMesgTool(),
    ProcessTool(),
    VMStatTool(),
    GoPProfTool(),
    PythonDiagTool(),
    CPPDiagTool(),
    JavaDiagTool(),
    StaticAnalysisTool(),
]
