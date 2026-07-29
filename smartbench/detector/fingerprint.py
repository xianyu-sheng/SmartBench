"""
Project fingerprint — the result of Phase 1 project scanning.

This is a pure data model. No LLM calls, no I/O.
All fields are populated by ProjectScanner from deterministic file-system signals.
"""

from dataclasses import dataclass, field
from datetime import datetime
from enum import Enum
from pathlib import Path
from typing import Dict, List


class Language(Enum):
    """Programming language detected in the project."""
    UNKNOWN = "unknown"
    PYTHON = "python"
    GO = "go"
    RUST = "rust"
    CPP = "cpp"
    C = "c"
    JAVA = "java"
    KOTLIN = "kotlin"
    JAVASCRIPT = "javascript"
    TYPESCRIPT = "typescript"
    RUBY = "ruby"
    SWIFT = "swift"
    CSHARP = "csharp"
    ZIG = "zig"
    MIXED = "mixed"  # multiple primary languages


class Framework(Enum):
    """Web / application framework inferred from dependencies."""
    NONE = "none"
    # Python
    FASTAPI = "fastapi"
    FLASK = "flask"
    DJANGO = "django"
    # Go
    GIN = "gin"
    ECHO = "echo"
    FIBER = "fiber"
    KIT = "kit"  # go-kit
    ZERO = "zero"  # go-zero
    KRATOS = "kratos"
    # JS / TS
    EXPRESS = "express"
    NESTJS = "nestjs"
    NEXTJS = "nextjs"
    REACT = "react"
    VUE = "vue"
    # Java
    SPRING = "spring"
    # Rust
    AXUM = "axum"
    ACTIX = "actix"
    ROCKET = "rocket"
    # C++
    GRPC = "grpc"
    BRPC = "brpc"
    # Generic
    CLI = "cli"
    LIBRARY = "library"
    MICROSERVICE = "microservice"


class ProjectType(Enum):
    """High-level project category."""
    UNKNOWN = "unknown"
    WEB_SERVICE = "web_service"        # HTTP / REST API
    RPC_SERVICE = "rpc_service"        # gRPC / Thrift / tRPC
    CLI_TOOL = "cli_tool"             # command-line application
    LIBRARY = "library"               # reusable package / SDK
    DATA_PIPELINE = "data_pipeline"   # ETL / stream processing
    DISTRIBUTED_SYSTEM = "distributed_system"  # consensus / coordination
    DATABASE = "database"             # DBMS / storage engine
    MOBILE_APP = "mobile_app"
    DESKTOP_APP = "desktop_app"
    EMBEDDED = "embedded"             # firmware / IoT
    INFRASTRUCTURE = "infrastructure"  # k8s operator, terraform, etc.


@dataclass
class ProjectFingerprint:
    """
    Deterministic fingerprint of a codebase — produced by ProjectScanner
    with zero LLM calls.

    All signals come from file-system heuristics:
      - Dependency manifests (go.mod, Cargo.toml, package.json, etc.)
      - Directory layout conventions
      - Build system files
      - Config file patterns
    """

    # Identity
    project_path: Path
    project_name: str = ""

    # Primary language (the dominant language by file count)
    primary_language: Language = Language.UNKNOWN
    secondary_languages: List[Language] = field(default_factory=list)
    language_confidence: float = 0.0  # 0..1

    # Framework & type
    framework: Framework = Framework.NONE
    framework_confidence: float = 0.0
    project_type: ProjectType = ProjectType.UNKNOWN

    # Build system
    build_system: str = ""            # e.g. "cargo", "go_modules", "maven", "pip"
    build_commands: List[str] = field(default_factory=list)

    # Size signals
    total_files: int = 0
    source_files: int = 0
    lines_of_code_estimate: int = 0
    scan_truncated: bool = False
    scan_file_limit: int = 0

    # Key files discovered
    manifest_files: List[str] = field(default_factory=list)   # go.mod, Cargo.toml, ...
    config_files: List[str] = field(default_factory=list)     # config.yaml, .env, ...
    entry_points: List[str] = field(default_factory=list)     # main.go, src/main.rs, ...
    test_dirs: List[str] = field(default_factory=list)        # tests/, __tests__/, ...

    # README signals (path exists, not content analysis — content analysis is LLM)
    has_readme: bool = False
    readme_path: str = ""

    # Git signals
    is_git_repo: bool = False
    git_remote_url: str = ""
    recent_commit_count: int = 0
    hot_files: List[str] = field(default_factory=list)  # files changed in last N commits

    # Dependency signals
    dependencies: List[str] = field(default_factory=list)     # key dependency names
    dependency_count: int = 0

    # Timestamp
    scanned_at: str = field(default_factory=lambda: datetime.now().isoformat())

    def to_dict(self) -> Dict:
        """Serialize to a dict for prompt injection / caching."""
        return {
            "project_name": self.project_name,
            "project_path": str(self.project_path),
            "primary_language": self.primary_language.value,
            "secondary_languages": [lang.value for lang in self.secondary_languages],
            "language_confidence": self.language_confidence,
            "framework": self.framework.value,
            "framework_confidence": self.framework_confidence,
            "project_type": self.project_type.value,
            "build_system": self.build_system,
            "total_files": self.total_files,
            "source_files": self.source_files,
            "lines_of_code_estimate": self.lines_of_code_estimate,
            "scan_truncated": self.scan_truncated,
            "scan_file_limit": self.scan_file_limit,
            "manifest_files": self.manifest_files,
            "entry_points": self.entry_points,
            "has_readme": self.has_readme,
            "readme_path": self.readme_path,
            "is_git_repo": self.is_git_repo,
            "git_remote_url": self.git_remote_url,
            "recent_commit_count": self.recent_commit_count,
            "hot_files": self.hot_files,
            "dependencies": self.dependencies[:30],
            "dependency_count": self.dependency_count,
            "scanned_at": self.scanned_at,
        }

    def summary(self) -> str:
        """One-line summary for CLI display."""
        parts = [f"[{self.primary_language.value}]"]
        if self.framework != Framework.NONE:
            parts.append(f"[{self.framework.value}]")
        parts.append(f"[{self.project_type.value}]")
        source_count = f">={self.source_files}" if self.scan_truncated else str(
            self.source_files
        )
        parts.append(f"{source_count} src files")
        if self.is_git_repo:
            parts.append("(git)")
        return " ".join(parts)
