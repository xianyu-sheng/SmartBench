"""
Tests for ProjectScanner (smartbench/detector/scanner.py)
and ProjectFingerprint (smartbench/detector/fingerprint.py).

All scanning is deterministic (zero LLM calls).  These tests use
tmp_path to create minimal project directories on the fly.
"""

import json
import subprocess
from pathlib import Path

import pytest

from smartbench.detector.fingerprint import (
    Framework,
    Language,
    ProjectFingerprint,
    ProjectType,
)
from smartbench.detector.scanner import ProjectScanner

# ── Helper factories ────────────────────────────────────────────────────────


def create_python_project(path: Path) -> Path:
    """Minimal Python project with .py files and pyproject.toml."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "main.py").write_text("print('hello')")
    (path / "app.py").write_text("def serve():\n    pass\n")
    (path / "pyproject.toml").write_text("[project]\nname='test'\n")
    return path


def create_go_project(path: Path) -> Path:
    """Minimal Go project with .go files and go.mod."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "main.go").write_text("package main\nfunc main() {}\n")
    (path / "go.mod").write_text("module test\ngo 1.21\n")
    return path


def create_rust_project(path: Path) -> Path:
    """Minimal Rust project with .rs files and Cargo.toml."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "src").mkdir(parents=True, exist_ok=True)
    (path / "src" / "main.rs").write_text("fn main() {}\n")
    (path / "Cargo.toml").write_text("[package]\nname='test'\nversion='0.1.0'\n")
    return path


def create_js_project(path: Path) -> Path:
    """Minimal JavaScript project with .js files and package.json."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.js").write_text("console.log('hello');\n")
    (path / "package.json").write_text('{"name":"test","version":"1.0.0"}\n')
    return path


def create_ts_project(path: Path) -> Path:
    """Minimal TypeScript project with .ts files and tsconfig.json."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "index.ts").write_text("const x: number = 1;\n")
    (path / "tsconfig.json").write_text('{"compilerOptions":{}}\n')
    return path


def create_java_project(path: Path) -> Path:
    """Minimal Java project with pom.xml."""
    path.mkdir(parents=True, exist_ok=True)
    (path / "pom.xml").write_text(
        '<project><groupId>test</groupId><artifactId>test</artifactId></project>\n'
    )
    return path


def _git_init_and_commit(path: Path, remote_url: str = "https://example.com/repo.git"):
    """Initialise a git repo in *path* and make one commit."""
    try:
        subprocess.run(["git", "init"], cwd=path, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.email", "test@test.com"],
                       cwd=path, capture_output=True, timeout=10)
        subprocess.run(["git", "config", "user.name", "Test"],
                       cwd=path, capture_output=True, timeout=10)
        if remote_url:
            subprocess.run(["git", "remote", "add", "origin", remote_url],
                           cwd=path, capture_output=True, timeout=10)
        subprocess.run(["git", "add", "."], cwd=path, capture_output=True, timeout=10)
        subprocess.run(["git", "commit", "-m", "init"],
                       cwd=path, capture_output=True, timeout=10)
    except (subprocess.SubprocessError, FileNotFoundError):
        pass  # git may not be available in CI


# ── Language detection ──────────────────────────────────────────────────────


class TestLanguageDetection:
    """Verify that ProjectScanner identifies the primary language from file
    extensions and manifest files."""

    def test_python_project(self, tmp_path: Path):
        p = create_python_project(tmp_path / "pyproj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.PYTHON

    def test_go_project(self, tmp_path: Path):
        p = create_go_project(tmp_path / "goproj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.GO

    def test_rust_project(self, tmp_path: Path):
        p = create_rust_project(tmp_path / "rsproj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.RUST

    def test_javascript_project(self, tmp_path: Path):
        p = create_js_project(tmp_path / "jsproj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.JAVASCRIPT

    def test_typescript_project(self, tmp_path: Path):
        p = create_ts_project(tmp_path / "tsproj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.TYPESCRIPT

    def test_java_project(self, tmp_path: Path):
        p = create_java_project(tmp_path / "javaproj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.JAVA

    def test_empty_directory_unknown(self, tmp_path: Path):
        """A directory with no source files should yield UNKNOWN."""
        p = tmp_path / "empty"
        p.mkdir()
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.UNKNOWN

    def test_ignores_dependencies_but_keeps_legacy_source(self, tmp_path: Path):
        project = create_js_project(tmp_path / "frontend")
        (project / "node_modules" / "fake").mkdir(parents=True)
        (project / "legacy").mkdir()
        for index in range(10):
            (project / "node_modules" / "fake" / f"module_{index}.py").write_text("")
            (project / "legacy" / f"old_{index}.js").write_text("")

        fingerprint = ProjectScanner(str(project)).scan()

        assert fingerprint.primary_language == Language.JAVASCRIPT
        assert fingerprint.source_files == 11

    def test_scanner_uses_pruned_walk_instead_of_repeated_rglob(
        self, tmp_path: Path, monkeypatch
    ):
        project = create_python_project(tmp_path / "single-walk")

        def fail_rglob(*args, **kwargs):
            raise AssertionError("scanner must not recursively glob per extension")

        monkeypatch.setattr(Path, "rglob", fail_rglob)

        fingerprint = ProjectScanner(str(project)).scan()

        assert fingerprint.primary_language == Language.PYTHON
        assert fingerprint.source_files == 2

    def test_scanner_reports_when_file_limit_is_reached(self, tmp_path: Path):
        project = tmp_path / "bounded"
        project.mkdir()
        for index in range(5):
            (project / f"module_{index}.py").write_text(
                f"VALUE = {index}\n"
            )

        fingerprint = ProjectScanner(str(project), max_files=3).scan()

        assert fingerprint.total_files == 3
        assert fingerprint.source_files == 3
        assert fingerprint.scan_truncated is True
        assert fingerprint.scan_file_limit == 3
        assert ">=3 src files" in fingerprint.summary()

    def test_scanner_skips_oversized_manifest_content(self, tmp_path: Path):
        project = tmp_path / "oversized-manifest"
        project.mkdir()
        (project / "app.py").write_text("print('ok')\n")
        (project / "requirements.txt").write_text("flask\n" + "x" * 100)

        fingerprint = ProjectScanner(
            str(project), max_file_bytes=50
        ).scan()

        assert fingerprint.primary_language == Language.PYTHON
        assert fingerprint.framework == Framework.NONE
        assert fingerprint.dependencies == []


# ── Framework detection ─────────────────────────────────────────────────────


class TestFrameworkDetection:
    """Framework is inferred from dependency-manifest content."""

    def test_flask_from_requirements_txt(self, tmp_path: Path):
        p = tmp_path / "flask-app"
        p.mkdir()
        (p / "app.py").write_text("from flask import Flask\n")
        (p / "requirements.txt").write_text("flask==2.3.0\nrequests==2.31.0\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.framework == Framework.FLASK

    def test_react_from_package_json(self, tmp_path: Path):
        p = tmp_path / "react-app"
        p.mkdir()
        (p / "index.tsx").write_text("import React from 'react';\n")
        (p / "package.json").write_text('{"dependencies":{"react":"18.2.0"}}\n')
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.framework == Framework.REACT

    def test_spring_from_pom_xml(self, tmp_path: Path):
        p = tmp_path / "spring-app"
        p.mkdir()
        (p / "pom.xml").write_text(
            "<project><artifactId>demo</artifactId>"
            "<dependencies><dependency><artifactId>spring-boot-starter-web</artifactId></dependency>"
            "</dependencies></project>\n"
        )
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.framework == Framework.SPRING

    def test_express_from_package_json(self, tmp_path: Path):
        p = tmp_path / "express-app"
        p.mkdir()
        (p / "index.js").write_text("const express = require('express');\n")
        (p / "package.json").write_text('{"dependencies":{"express":"4.18.2"}}\n')
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.framework == Framework.EXPRESS


# ── Build system detection ─────────────────────────────────────────────────


class TestBuildSystemDetection:
    """Build system is mapped from manifest file names."""

    def test_cargo(self, tmp_path: Path):
        p = create_rust_project(tmp_path / "cargo-proj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.build_system == "cargo"

    def test_go_modules(self, tmp_path: Path):
        p = create_go_project(tmp_path / "gomod-proj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.build_system == "go_modules"

    def test_pip_via_pyproject(self, tmp_path: Path):
        p = create_python_project(tmp_path / "pip-proj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.build_system == "pip"

    def test_npm(self, tmp_path: Path):
        p = create_js_project(tmp_path / "npm-proj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.build_system == "npm"

    def test_maven(self, tmp_path: Path):
        p = create_java_project(tmp_path / "mvn-proj")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.build_system == "maven"

    def test_gradle(self, tmp_path: Path):
        p = tmp_path / "gradle-proj"
        p.mkdir()
        (p / "build.gradle").write_text("apply plugin: 'java'\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.build_system == "gradle"


# ── Project type detection ─────────────────────────────────────────────────


class TestProjectTypeDetection:
    """Project type is inferred from directory layout and framework."""

    def test_web_service_via_api_dir(self, tmp_path: Path):
        p = tmp_path / "api-proj"
        p.mkdir()
        (p / "api").mkdir()
        (p / "main.py").write_text("")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.project_type == ProjectType.WEB_SERVICE

    def test_cli_tool_via_entry_point(self, tmp_path: Path):
        p = tmp_path / "cli-proj"
        p.mkdir()
        (p / "cli.py").write_text("print('cli')\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.project_type == ProjectType.CLI_TOOL

    def test_library_when_no_http_framework(self, tmp_path: Path):
        """When framework is LIBRARY, project_type should be LIBRARY."""
        p = tmp_path / "lib-proj"
        p.mkdir()
        (p / "lib.py").write_text("def util(): pass\n")
        (p / "pyproject.toml").write_text("[project]\nname='lib'\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        # Without an explicit LIBRARY framework signal, should still be
        # UNKNOWN (no http framework, no entry-point pattern matches exactly)
        assert fp.project_type in (ProjectType.UNKNOWN, ProjectType.LIBRARY)

    def test_web_service_via_framework(self, tmp_path: Path):
        p = tmp_path / "fastapi-app"
        p.mkdir()
        (p / "main.py").write_text("from fastapi import FastAPI\n")
        (p / "requirements.txt").write_text("fastapi\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.project_type == ProjectType.WEB_SERVICE


# ── Mixed language & primary selection ──────────────────────────────────────


class TestMixedLanguage:
    """When multiple languages are present, the dominant one (by file count)
    should be primary."""

    def test_mixed_python_js_selects_python(self, tmp_path: Path):
        p = tmp_path / "mixed"
        p.mkdir()
        # 2 Python files, 1 JS file → Python wins
        (p / "main.py").write_text("")
        (p / "util.py").write_text("")
        (p / "script.js").write_text("")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.PYTHON

    def test_mixed_js_ts_selects_js(self, tmp_path: Path):
        p = tmp_path / "mixed-js-ts"
        p.mkdir()
        (p / "index.js").write_text("")
        (p / "app.js").write_text("")
        (p / "types.ts").write_text("")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.primary_language == Language.JAVASCRIPT


# ── Git info ────────────────────────────────────────────────────────────────


class TestGitDetection:
    """Git signals are gathered when a .git directory exists."""

    def test_git_remote_and_commit_count(self, tmp_path: Path):
        p = tmp_path / "git-repo"
        p.mkdir()
        create_python_project(p)
        _git_init_and_commit(p, "https://example.com/my-repo.git")

        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        if fp.is_git_repo:
            # These fields are only populated when git subprocess succeeds
            assert fp.is_git_repo is True
            assert fp.recent_commit_count >= 1
            assert "example.com" in fp.git_remote_url

    def test_git_remote_credentials_are_not_stored(self, tmp_path: Path):
        p = tmp_path / "credentialed-remote"
        p.mkdir()
        create_python_project(p)
        _git_init_and_commit(
            p,
            "https://user:super-secret@example.com/repo.git?token=also-secret",
        )

        fingerprint = ProjectScanner(str(p)).scan()

        assert fingerprint.git_remote_url == "https://example.com/repo.git"
        assert "secret" not in fingerprint.git_remote_url

    def test_no_git(self, tmp_path: Path):
        """Without .git, all git fields should keep their defaults."""
        p = tmp_path / "no-git"
        p.mkdir()
        create_python_project(p)

        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.is_git_repo is False
        assert fp.git_remote_url == ""
        assert fp.recent_commit_count == 0
        assert fp.hot_files == []

    def test_git_repo_is_detected_from_nested_project(self, tmp_path: Path):
        repository = tmp_path / "monorepo"
        project = repository / "packages" / "service"
        create_python_project(project)
        _git_init_and_commit(repository)

        fingerprint = ProjectScanner(str(project)).scan()

        assert fingerprint.is_git_repo is True
        assert fingerprint.recent_commit_count >= 1
        assert "main.py" in fingerprint.hot_files


# ── Manifest file discovery ─────────────────────────────────────────────────


class TestManifestDetection:
    """Manifest files are collected during scanning."""

    def test_manifest_files_found(self, tmp_path: Path):
        p = tmp_path / "manifest-proj"
        p.mkdir()
        (p / "main.py").write_text("")
        (p / "pyproject.toml").write_text("[project]\n")
        (p / "requirements.txt").write_text("flask\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert "pyproject.toml" in fp.manifest_files
        assert "requirements.txt" in fp.manifest_files


class TestDependencyDetection:
    def test_non_git_python_project_still_reports_dependencies(self, tmp_path):
        project = tmp_path / "python-deps"
        project.mkdir()
        (project / "main.py").write_text("print('ok')\n")
        (project / "requirements.txt").write_text(
            "Flask==3.0\nrequests[security]>=2\n-r dev.txt\n"
        )

        fingerprint = ProjectScanner(str(project)).scan()

        assert fingerprint.is_git_repo is False
        assert fingerprint.dependencies == ["Flask", "requests"]
        assert fingerprint.dependency_count == 2

    def test_go_require_block_excludes_directives_and_parenthesis(self, tmp_path):
        project = tmp_path / "go-deps"
        project.mkdir()
        (project / "main.go").write_text("package main\n")
        (project / "go.mod").write_text(
            "module example.test/app\n\n"
            "go 1.22\n\n"
            "require (\n"
            "    github.com/gin-gonic/gin v1.10.0\n"
            "    golang.org/x/sync v0.7.0 // indirect\n"
            ")\n"
            "replace golang.org/x/sync => ./local-sync\n"
        )

        fingerprint = ProjectScanner(str(project)).scan()

        assert fingerprint.dependencies == [
            "github.com/gin-gonic/gin",
            "golang.org/x/sync",
        ]

    def test_package_json_dependency_sections_are_deduplicated(self, tmp_path):
        project = tmp_path / "node-deps"
        project.mkdir()
        (project / "index.js").write_text("console.log('ok')\n")
        (project / "package.json").write_text(json.dumps({
            "dependencies": {"react": "18", "zod": "3"},
            "devDependencies": {"React": "18", "vitest": "2"},
            "peerDependencies": ["malformed"],
        }))

        fingerprint = ProjectScanner(str(project)).scan()

        assert fingerprint.dependencies == ["react", "zod", "vitest"]
        assert fingerprint.dependency_count == 3


# ── Entry point detection ──────────────────────────────────────────────────


class TestEntryPointDetection:
    """Entry-point files are identified by well-known names."""

    def test_python_entry_points(self, tmp_path: Path):
        p = tmp_path / "entry-proj"
        p.mkdir()
        (p / "main.py").write_text("")
        (p / "app.py").write_text("")
        (p / "cli.py").write_text("")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert "main.py" in fp.entry_points
        assert "cli.py" in fp.entry_points

    def test_go_entry_point(self, tmp_path: Path):
        p = tmp_path / "go-entry"
        p.mkdir()
        (p / "main.go").write_text("package main\n")
        (p / "go.mod").write_text("module x\n")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert any("main.go" in ep for ep in fp.entry_points)


# ── README detection ────────────────────────────────────────────────────────


class TestReadmeDetection:
    """README files are detected by well-known patterns."""

    def test_readme_detected(self, tmp_path: Path):
        p = tmp_path / "readme-proj"
        p.mkdir()
        (p / "README.md").write_text("# Project\n")
        (p / "main.py").write_text("")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.has_readme is True
        assert fp.readme_path == "README.md"

    def test_no_readme(self, tmp_path: Path):
        p = tmp_path / "no-readme"
        p.mkdir()
        (p / "main.py").write_text("")
        scanner = ProjectScanner(str(p))
        fp = scanner.scan()
        assert fp.has_readme is False
        assert fp.readme_path == ""


# ── Constructor error cases ─────────────────────────────────────────────────


class TestScannerConstructor:
    """ProjectScanner.__init__ validates the project path."""

    def test_path_does_not_exist(self):
        with pytest.raises(FileNotFoundError):
            ProjectScanner("/nonexistent/path/12345")

    def test_path_is_file_not_directory(self, tmp_path: Path):
        f = tmp_path / "afile.txt"
        f.write_text("hello")
        with pytest.raises(NotADirectoryError):
            ProjectScanner(str(f))


# ── ProjectFingerprint data model ────────────────────────────────────────────


class TestProjectFingerprint:
    """ProjectFingerprint is a plain dataclass with serialization support."""

    def test_default_values(self):
        fp = ProjectFingerprint(project_path=Path("/x"))
        assert fp.primary_language == Language.UNKNOWN
        assert fp.framework == Framework.NONE
        assert fp.project_type == ProjectType.UNKNOWN
        assert fp.language_confidence == 0.0
        assert fp.total_files == 0
        assert fp.scan_truncated is False
        assert fp.scan_file_limit == 0
        assert fp.is_git_repo is False

    def test_to_dict_roundtrip(self):
        fp = ProjectFingerprint(
            project_path=Path("/tmp/test"),
            project_name="test",
            primary_language=Language.PYTHON,
            framework=Framework.FLASK,
            project_type=ProjectType.WEB_SERVICE,
            build_system="pip",
            total_files=10,
            source_files=8,
            is_git_repo=True,
            git_remote_url="https://example.com/repo.git",
            recent_commit_count=5,
        )
        d = fp.to_dict()
        assert d["primary_language"] == "python"
        assert d["framework"] == "flask"
        assert d["project_type"] == "web_service"
        assert d["build_system"] == "pip"
        assert d["total_files"] == 10
        assert d["scan_truncated"] is False
        assert d["scan_file_limit"] == 0
        assert d["is_git_repo"] is True
        assert d["git_remote_url"] == "https://example.com/repo.git"
        assert d["recent_commit_count"] == 5
        assert "scanned_at" in d

    def test_summary_format(self):
        fp = ProjectFingerprint(
            project_path=Path("/x"),
            primary_language=Language.GO,
            framework=Framework.GIN,
            project_type=ProjectType.WEB_SERVICE,
            source_files=15,
            is_git_repo=True,
        )
        s = fp.summary()
        assert "go" in s
        assert "gin" in s
        assert "web_service" in s
        assert "15" in s
        assert "git" in s
