"""
ProjectScanner — deterministic, zero-LLM project fingerprinting.

Walks the filesystem once and populates a ProjectFingerprint
from manifest files, directory conventions, build systems, and git history.
"""

import subprocess
from pathlib import Path
from typing import Dict, List, Optional, Tuple
from urllib.parse import urlsplit, urlunsplit

from smartbench.detector.fingerprint import (
    Framework,
    Language,
    ProjectFingerprint,
    ProjectType,
)
from smartbench.path_safety import is_project_file, resolve_project_file

_EXCLUDED_DIRS = {
    ".git", "node_modules", "__pycache__", "target", "build", "vendor",
    ".venv", "venv", "dist", ".idea", ".vscode", "obj", ".tox",
    ".eggs", ".smartbench", ".pytest_cache", ".mypy_cache",
    ".ruff_cache",
}


def _is_excluded(root: Path, path: Path) -> bool:
    try:
        relative = path.relative_to(root)
    except ValueError:
        return True
    return bool(set(relative.parts[:-1]) & _EXCLUDED_DIRS)


def _sanitize_git_remote(remote: str) -> str:
    """Remove URL credentials, query parameters, and fragments from remotes."""
    value = remote.strip()
    if not value:
        return ""
    try:
        parsed = urlsplit(value)
    except ValueError:
        return "<invalid remote>"
    if parsed.scheme and parsed.netloc:
        hostname = parsed.hostname or ""
        if ":" in hostname and not hostname.startswith("["):
            hostname = f"[{hostname}]"
        try:
            port = parsed.port
        except ValueError:
            port = None
        host = f"{hostname}:{port}" if port else hostname
        username = parsed.username if parsed.scheme not in {"http", "https"} else None
        netloc = f"{username}@{host}" if username else host
        return urlunsplit((parsed.scheme, netloc, parsed.path, "", ""))

    # SCP-style remotes normally use ``git@host:path``. If the userinfo
    # contains a colon, treat it as a credential pair and remove it entirely.
    if "@" in value:
        userinfo, host_path = value.rsplit("@", 1)
        if ":" in userinfo:
            return f"***@{host_path.split('?', 1)[0].split('#', 1)[0]}"
    return value.split("?", 1)[0].split("#", 1)[0]

# ── Language detection: extension → Language ──────────────────────────
_EXTENSION_MAP: Dict[str, Language] = {
    ".py": Language.PYTHON,
    ".go": Language.GO,
    ".rs": Language.RUST,
    ".cpp": Language.CPP, ".cc": Language.CPP, ".cxx": Language.CPP, ".c++": Language.CPP,
    ".c": Language.C, ".h": Language.C,
    ".java": Language.JAVA,
    ".kt": Language.KOTLIN, ".kts": Language.KOTLIN,
    ".js": Language.JAVASCRIPT, ".mjs": Language.JAVASCRIPT, ".cjs": Language.JAVASCRIPT,
    ".ts": Language.TYPESCRIPT, ".tsx": Language.TYPESCRIPT,
    ".rb": Language.RUBY,
    ".swift": Language.SWIFT,
    ".cs": Language.CSHARP,
    ".zig": Language.ZIG,
}

# Manifest files → deterministic language + framework signal
_MANIFEST_MAP: Dict[str, Tuple[Language, Optional[Framework]]] = {
    "go.mod": (Language.GO, None),            # framework inferred from deps later
    "Cargo.toml": (Language.RUST, None),
    "CMakeLists.txt": (Language.CPP, None),
    "Makefile": (Language.UNKNOWN, None),  # Too generic to infer language
    "pom.xml": (Language.JAVA, None),
    "build.gradle": (Language.JAVA, None),
    "build.gradle.kts": (Language.KOTLIN, None),
    "package.json": (Language.JAVASCRIPT, None),
    "tsconfig.json": (Language.TYPESCRIPT, None),
    "requirements.txt": (Language.PYTHON, None),
    "pyproject.toml": (Language.PYTHON, None),
    "setup.py": (Language.PYTHON, None),
    "Pipfile": (Language.PYTHON, None),
    "Gemfile": (Language.RUBY, None),
    "Package.swift": (Language.SWIFT, None),
    "build.zig": (Language.ZIG, None),
}

# Framework detection from go.mod / package.json / requirements.txt content
_FRAMEWORK_SIGNALS: Dict[str, List[Tuple[str, Framework]]] = {
    "go.mod": [
        ("gin-gonic/gin", Framework.GIN),
        ("labstack/echo", Framework.ECHO),
        ("gofiber/fiber", Framework.FIBER),
        ("go-kit/kit", Framework.KIT),
        ("zeromicro/go-zero", Framework.ZERO),
        ("go-kratos/kratos", Framework.KRATOS),
        ("grpc/grpc-go", Framework.GRPC),
    ],
    "Cargo.toml": [
        ("actix-web", Framework.ACTIX),
        ("axum", Framework.AXUM),
        ("rocket", Framework.ROCKET),
        ("tonic", Framework.GRPC),
    ],
    "requirements.txt": [
        ("fastapi", Framework.FASTAPI),
        ("flask", Framework.FLASK),
        ("django", Framework.DJANGO),
    ],
    "package.json": [
        ("express", Framework.EXPRESS),
        ("@nestjs/core", Framework.NESTJS),
        ("next", Framework.NEXTJS),
        ("react", Framework.REACT),
        ("vue", Framework.VUE),
    ],
    "pom.xml": [
        ("spring-boot-starter", Framework.SPRING),
    ],
    "build.gradle": [
        ("spring-boot", Framework.SPRING),
    ],
}

# Entry point files → project type signal
_ENTRY_POINT_PATTERNS: Dict[str, ProjectType] = {
    "main.go": ProjectType.WEB_SERVICE,       # heuristic: most Go main.go are services
    "src/main/java": ProjectType.WEB_SERVICE,  # Java convention
    "app.py": ProjectType.WEB_SERVICE,
    "server.py": ProjectType.WEB_SERVICE,
    "cli.py": ProjectType.CLI_TOOL,
    "start.py": ProjectType.CLI_TOOL,
    "run.py": ProjectType.CLI_TOOL,
    "index.js": ProjectType.WEB_SERVICE,
    "index.ts": ProjectType.WEB_SERVICE,
    "main.cpp": ProjectType.CLI_TOOL,
    "Program.cs": ProjectType.WEB_SERVICE,
}


class ProjectScanner:
    """
    Scans a project directory deterministically — zero LLM calls.

    Usage:
        scanner = ProjectScanner("/path/to/project")
        fingerprint = scanner.scan()
        print(fingerprint.summary())
    """

    def __init__(self, project_path: str):
        self.root = Path(project_path).resolve()
        if not self.root.exists():
            raise FileNotFoundError(f"Project path does not exist: {self.root}")
        if not self.root.is_dir():
            raise NotADirectoryError(f"Not a directory: {self.root}")

    # ── Public API ────────────────────────────────────────────────────

    def scan(self) -> ProjectFingerprint:
        """Run all detection passes and return a complete fingerprint."""
        fp = ProjectFingerprint(
            project_path=self.root,
            project_name=self.root.name,
        )

        self._detect_languages(fp)
        self._detect_manifests(fp)
        self._detect_framework(fp)
        self._detect_build_system(fp)
        self._detect_project_type(fp)
        self._count_files(fp)
        self._detect_readme(fp)
        self._detect_git(fp)
        self._detect_configs(fp)
        self._detect_entry_points(fp)

        # Refine type if CLI patterns are strong
        if fp.project_type == ProjectType.UNKNOWN and fp.framework == Framework.CLI:
            fp.project_type = ProjectType.CLI_TOOL

        return fp

    # ── Detection passes ──────────────────────────────────────────────

    def _detect_languages(self, fp: ProjectFingerprint) -> None:
        """Walk root (depth-limited) and count file extensions."""
        counts: Dict[Language, int] = {}
        for ext, lang in _EXTENSION_MAP.items():
            # Quick glob per extension
            try:
                count = sum(
                    1 for path in self.root.rglob(f"*{ext}")
                    if is_project_file(self.root, path)
                    and not _is_excluded(self.root, path)
                )
                if count > 0:
                    counts[lang] = counts.get(lang, 0) + count
            except (OSError, PermissionError):
                pass

        if not counts:
            return

        total = sum(counts.values())
        sorted_langs = sorted(counts.items(), key=lambda x: x[1], reverse=True)

        fp.primary_language = sorted_langs[0][0]
        fp.language_confidence = sorted_langs[0][1] / total if total > 0 else 0.0

        for lang, count in sorted_langs[1:]:
            ratio = count / total if total > 0 else 0
            if ratio > 0.15:  # significant minority
                fp.secondary_languages.append(lang)

        # Mark mixed if no clear dominant
        if fp.language_confidence < 0.6 and len(sorted_langs) >= 2:
            if sorted_langs[0][1] / total < 0.7:
                fp.secondary_languages.append(fp.primary_language)
                fp.primary_language = Language.MIXED

    def _detect_manifests(self, fp: ProjectFingerprint) -> None:
        """Find manifest / dependency files at root and common subdirs."""
        search_dirs = [self.root] + list(self.root.glob("*"))[:20]

        # Manifest files that should NOT override language (too generic)
        GENERIC_MANIFESTS = {"Makefile"}  # noqa: N806

        for d in search_dirs:
            if not d.is_dir():
                continue
            for manifest_name, (lang, _) in _MANIFEST_MAP.items():
                candidate = d / manifest_name
                if is_project_file(self.root, candidate):
                    fp.manifest_files.append(str(candidate.relative_to(self.root)))

                    # If language wasn't detected from extensions, use manifest signal
                    # EXCEPT for generic manifests that don't strongly indicate a language
                    if (fp.primary_language == Language.UNKNOWN
                            and manifest_name not in GENERIC_MANIFESTS):
                        fp.primary_language = lang
                        fp.language_confidence = 0.8

        # Deduplicate
        fp.manifest_files = sorted(set(fp.manifest_files))

    def _detect_framework(self, fp: ProjectFingerprint) -> None:
        """Parse manifest file contents for framework signals."""
        if not fp.manifest_files:
            return

        for manifest_rel in fp.manifest_files:
            manifest_path = resolve_project_file(self.root, manifest_rel)
            if manifest_path is None:
                continue
            manifest_name = manifest_path.name

            signals = _FRAMEWORK_SIGNALS.get(manifest_name, [])
            if not signals:
                # Also check generic signals from pyproject.toml / requirements.txt
                if manifest_name in ("requirements.txt", "pyproject.toml"):
                    signals = _FRAMEWORK_SIGNALS.get("requirements.txt", [])

            try:
                content = manifest_path.read_text(encoding="utf-8", errors="ignore").lower()
            except (OSError, PermissionError):
                continue

            for keyword, framework in signals:
                if keyword.lower() in content:
                    fp.framework = framework
                    fp.framework_confidence = 0.9
                    return

    def _detect_build_system(self, fp: ProjectFingerprint) -> None:
        """Identify the build system from manifest files."""
        manifest_names = {Path(m).name for m in fp.manifest_files}

        # Map manifest file name → build system name + default commands
        BUILD_MAP = {  # noqa: N806
            "go.mod": ("go_modules", ["go build ./...", "go test ./..."]),
            "Cargo.toml": ("cargo", ["cargo build --release", "cargo test"]),
            "CMakeLists.txt": ("cmake", ["cmake -B build && cmake --build build"]),
            "Makefile": ("make", ["make", "make test"]),
            "pom.xml": ("maven", ["mvn compile", "mvn test"]),
            "build.gradle": ("gradle", ["./gradlew build"]),
            "build.gradle.kts": ("gradle", ["./gradlew build"]),
            "package.json": ("npm", ["npm run build", "npm test"]),
            "pyproject.toml": ("pip", ["pip install -e .", "pytest"]),
            "requirements.txt": ("pip", ["pip install -r requirements.txt", "pytest"]),
            "setup.py": ("pip", ["pip install -e .", "pytest"]),
        }

        for name in manifest_names:
            if name in BUILD_MAP:
                bs, cmds = BUILD_MAP[name]
                fp.build_system = bs
                fp.build_commands = cmds
                return

    def _detect_project_type(self, fp: ProjectFingerprint) -> None:
        """Infer the project type from structure and dependencies."""
        root_dirs = {d.name.lower() for d in self.root.iterdir() if d.is_dir()}

        # Strong signals from directory layout
        if {"raft", "consensus", "paxos"} & root_dirs:
            fp.project_type = ProjectType.DISTRIBUTED_SYSTEM
            return
        if {"api", "handler", "middleware", "router"} & root_dirs:
            fp.project_type = ProjectType.WEB_SERVICE
            return
        if {"cmd", "pkg", "internal"} & root_dirs:
            fp.project_type = ProjectType.WEB_SERVICE  # Go standard layout
            if fp.framework == Framework.CLI:
                fp.project_type = ProjectType.CLI_TOOL
            return
        if {"src", "include", "lib"} & root_dirs and fp.primary_language == Language.CPP:
            fp.project_type = ProjectType.CLI_TOOL  # default for C++
            return
        if {"etl", "pipeline", "jobs", "dags"} & root_dirs:
            fp.project_type = ProjectType.DATA_PIPELINE
            return
        if {"terraform", "k8s", "charts", "deploy"} & root_dirs:
            fp.project_type = ProjectType.INFRASTRUCTURE
            return

        # Signals from entry points (only if framework doesn't already tell us)
        if fp.framework == Framework.NONE:
            for entry_file, proj_type in _ENTRY_POINT_PATTERNS.items():
                if (self.root / entry_file).exists():
                    fp.project_type = proj_type
                    return

        # Default heuristic: if it has an HTTP framework → web service
        if fp.framework in {Framework.FASTAPI, Framework.FLASK, Framework.DJANGO,
                            Framework.GIN, Framework.ECHO, Framework.FIBER,
                            Framework.EXPRESS, Framework.NESTJS, Framework.AXUM,
                            Framework.ACTIX, Framework.SPRING}:
            fp.project_type = ProjectType.WEB_SERVICE
            return

        if fp.framework == Framework.GRPC:
            fp.project_type = ProjectType.RPC_SERVICE
            return

        if fp.framework == Framework.CLI:
            fp.project_type = ProjectType.CLI_TOOL
            return

        if fp.framework == Framework.LIBRARY:
            fp.project_type = ProjectType.LIBRARY
            return

        # Fallback
        fp.project_type = ProjectType.UNKNOWN

    def _count_files(self, fp: ProjectFingerprint) -> None:
        """Count total files and source files (limited depth for performance)."""
        try:
            all_files = list(self.root.rglob("*"))
            # Filter out common non-source dirs
            files = []
            for f in all_files:
                if is_project_file(self.root, f) and not _is_excluded(self.root, f):
                    files.append(f)

            fp.total_files = len(files)

            # Count source files
            src_exts = set(_EXTENSION_MAP.keys())
            source_files = [f for f in files if f.suffix in src_exts]
            fp.source_files = len(source_files)

            # Rough LOC estimate (sample first 20 source files, extrapolate)
            sample_size = min(20, len(source_files))
            total_lines = 0
            for f in source_files[:sample_size]:
                try:
                    # Count non-blank lines
                    lines = f.read_text(encoding="utf-8", errors="ignore").count("\n")
                    total_lines += max(1, lines)
                except (OSError, PermissionError):
                    pass

            if sample_size > 0 and len(source_files) > 0:
                avg_lines = total_lines / sample_size
                fp.lines_of_code_estimate = int(avg_lines * len(source_files))
        except (OSError, PermissionError):
            pass

    def _detect_readme(self, fp: ProjectFingerprint) -> None:
        """Check for README existence (content analysis left to LLM)."""
        readme_patterns = ["README.md", "readme.md", "README", "README.rst",
                          "README.txt", "README.org", "README_CN.md", "README_zh.md"]
        for pattern in readme_patterns:
            candidate = self.root / pattern
            if resolve_project_file(self.root, pattern) is not None:
                fp.has_readme = True
                fp.readme_path = str(candidate.relative_to(self.root))
                return

    def _detect_configs(self, fp: ProjectFingerprint) -> None:
        """Find configuration files."""
        config_patterns = [
            "config.yaml", "config.yml", "config.toml", "config.json",
            ".env", ".env.example", "config.default.yaml",
            "application.yml", "application.properties",
            "appsettings.json",
            "docker-compose.yml", "docker-compose.yaml",
            "Dockerfile",
            ".gitlab-ci.yml", ".github",
            "terraform", "*.tf",
        ]
        for pattern in config_patterns:
            if "*" in pattern:
                candidates = list(self.root.glob(pattern))
                for c in candidates[:5]:
                    if is_project_file(self.root, c):
                        fp.config_files.append(str(c.relative_to(self.root)))
            else:
                candidate = self.root / pattern
                if (
                    not candidate.is_symlink()
                    and candidate.exists()
                    and candidate.resolve().is_relative_to(self.root)
                ):
                    fp.config_files.append(pattern)

        fp.config_files = sorted(set(fp.config_files))

    def _detect_entry_points(self, fp: ProjectFingerprint) -> None:
        """Identify entry-point files."""
        for pattern, _ in _ENTRY_POINT_PATTERNS.items():
            if resolve_project_file(self.root, pattern) is not None:
                fp.entry_points.append(pattern)

        # Language-specific entry points
        if fp.primary_language == Language.GO:
            for f in self.root.glob("**/main.go"):
                if is_project_file(self.root, f):
                    fp.entry_points.append(str(f.relative_to(self.root)))
        elif fp.primary_language == Language.PYTHON:
            for name in ["main.py", "app.py", "server.py", "cli.py", "run.py"]:
                if resolve_project_file(self.root, name) is not None:
                    fp.entry_points.append(name)
        elif fp.primary_language == Language.RUST:
            if resolve_project_file(self.root, "src/main.rs") is not None:
                fp.entry_points.append("src/main.rs")

        fp.entry_points = sorted(set(fp.entry_points))[:10]

    def _detect_git(self, fp: ProjectFingerprint) -> None:
        """Gather git signals without any LLM calls."""
        git_dir = self.root / ".git"
        if not git_dir.exists():
            return

        fp.is_git_repo = True

        # Remote URL
        try:
            result = subprocess.run(
                ["git", "remote", "get-url", "origin"],
                cwd=str(self.root), capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                fp.git_remote_url = _sanitize_git_remote(result.stdout)
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

        # Recent commit count
        try:
            result = subprocess.run(
                ["git", "rev-list", "--count", "HEAD", "--max-count=100"],
                cwd=str(self.root), capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                fp.recent_commit_count = int(result.stdout.strip())
        except (subprocess.SubprocessError, FileNotFoundError, OSError, ValueError):
            pass

        # Hot files (changed in last 10 commits)
        try:
            result = subprocess.run(
                ["git", "log", "--name-only", "--pretty=format:", "-n", "10"],
                cwd=str(self.root), capture_output=True, text=True, timeout=10,
            )
            if result.returncode == 0:
                files = [f.strip() for f in result.stdout.split("\n") if f.strip()]
                # Count occurrences and take top 5
                from collections import Counter
                fp.hot_files = [f for f, _ in Counter(files).most_common(10)]
        except (subprocess.SubprocessError, FileNotFoundError, OSError):
            pass

        # Dependencies
        self._detect_dependencies(fp)

    def _detect_dependencies(self, fp: ProjectFingerprint) -> None:
        """Extract dependency names from manifests."""
        # Go: go.mod
        go_mod = resolve_project_file(self.root, "go.mod")
        if go_mod is not None:
            try:
                content = go_mod.read_text(encoding="utf-8", errors="ignore")
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("module") and not line.startswith("go "):
                        if "require" in line:
                            continue
                        if "//" in line:
                            line = line[:line.index("//")].strip()
                        parts = line.split()
                        if parts:
                            fp.dependencies.append(parts[0].strip('"'))
                fp.dependency_count = len(fp.dependencies)
            except (OSError, PermissionError):
                pass

        # Python: requirements.txt
        req_file = resolve_project_file(self.root, "requirements.txt")
        if req_file is not None:
            try:
                content = req_file.read_text(encoding="utf-8", errors="ignore")
                for line in content.split("\n"):
                    line = line.strip()
                    if line and not line.startswith("#"):
                        pkg = line.split("==")[0].split(">=")[0].split("<")[0].strip()
                        if pkg:
                            fp.dependencies.append(pkg)
                fp.dependency_count = len(fp.dependencies)
            except (OSError, PermissionError):
                pass
