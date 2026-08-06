"""Tests for GitBranchChecker — cross-branch fix detection."""

import subprocess
from pathlib import Path

import pytest

from smartbench.frontends.git_branch_checker import GitBranchChecker


@pytest.fixture
def dual_branch_repo(tmp_path: Path) -> Path:
    """Create a minimal git repo with main + dev branches.

    main has a bug (missing defer Close); dev has the fix (defer Close added).
    """
    repo = tmp_path / "test-repo"
    repo.mkdir()

    def _git(*args: str) -> str:
        r = subprocess.run(
            ["git", "-C", str(repo)] + list(args),
            capture_output=True, text=True,
        )
        r.check_returncode()
        return r.stdout

    # Minimal Go-like source on main (no defer Close).
    main_src = """package leak

import "net"

func process(addr string) error {
    conn, err := net.Dial("tcp", addr)
    if err != nil {
        return err
    }
    // SendAndReceive would go here
    // BUG: conn never closed
    return nil
}
"""
    (repo / "leak.go").write_text(main_src)

    # Init git, create main branch.
    _git("init", "-b", "main")
    _git("config", "user.email", "test@test")
    _git("config", "user.name", "Test")
    _git("add", "leak.go")
    _git("commit", "-m", "initial")

    # Create dev branch with the fix (defer conn.Close()).
    fixed_src = """package leak

import "net"

func process(addr string) error {
    conn, err := net.Dial("tcp", addr)
    if err != nil {
        return err
    }
    defer conn.Close()
    // SendAndReceive would go here
    return nil
}
"""
    _git("checkout", "-b", "dev")
    (repo / "leak.go").write_text(fixed_src)
    _git("add", "leak.go")
    _git("commit", "-m", "fix: add defer conn.Close()")

    # Back to main.
    _git("checkout", "main")

    return repo


def test_already_fixed_on_dev(dual_branch_repo: Path):
    """Dev branch has extra Close → should be flagged as already-fixed."""
    checker = GitBranchChecker(dual_branch_repo)
    result = checker.check("leak.go", 5, 11)

    assert result.error is None, result.error
    assert "dev" in result.already_fixed_on, f"expected dev in {result.already_fixed_on}"
    assert result.function_name in (None, "process")


def test_clean_when_only_main(dual_branch_repo: Path):
    """Finding that matches both branches → not already-fixed."""
    # Add a function that's identical on both branches.
    identical = """package leak

func unchanged() error { return nil }
"""
    (dual_branch_repo / "ok.go").write_text(identical)
    subprocess.run(
        ["git", "-C", str(dual_branch_repo), "add", "ok.go"], check=True
    )
    subprocess.run(
        ["git", "-C", str(dual_branch_repo), "commit", "-m", "add ok"],
        check=True,
    )
    # Merge to dev so both branches have same code.
    subprocess.run(
        ["git", "-C", str(dual_branch_repo), "merge", "main", "-m", "merge"],
        check=True, cwd=str(dual_branch_repo),
    )
    subprocess.run(
        ["git", "-C", str(dual_branch_repo), "checkout", "main"], check=True
    )

    checker = GitBranchChecker(dual_branch_repo)
    result = checker.check("ok.go", 2, 4)

    assert result.error is None, result.error
    assert not result.already_fixed_on, f"unexpected: {result.already_fixed_on}"


def test_missing_file(dual_branch_repo: Path):
    """Non-existent file should report an error, not crash."""
    checker = GitBranchChecker(dual_branch_repo)
    result = checker.check("nonexistent.go", 1, 3)

    assert result.error is not None
    assert "not found" in result.error.lower() or "no such" in result.error.lower()
    assert not result.already_fixed_on


def test_check_findings_batch(dual_branch_repo: Path):
    """Batch-check multiple findings."""
    findings = [
        {
            "location": {"file_path": "leak.go", "line_start": 5, "line_end": 11},
            "title": "conn leak",
        },
        {
            "location": {"file_path": "nonexistent.go", "line_start": 1},
            "title": "bad path",
        },
        {
            # finding with no location → skipped
        },
    ]

    checker = GitBranchChecker(dual_branch_repo)
    results = checker.check_findings(findings)

    already, clean = GitBranchChecker.format_report(results)
    assert len(already) == 1  # leak.go is fixed on dev
    assert already[0].file_path == "leak.go"
    assert len(clean) == 0  # nonexistent is error, no-location skipped
    assert len(results) == 2  # only 2 have location data


def test_string_location_from_real_quick_output(dual_branch_repo: Path):
    """Real quick output uses ``path:line-range`` strings."""
    checker = GitBranchChecker(dual_branch_repo)
    results = checker.check_findings([
        {"location": "leak.go:5-11", "title": "conn leak"},
    ])
    assert len(results) == 1
    assert "dev" in results[0].already_fixed_on


def test_remote_only_dev_branch(tmp_path: Path, dual_branch_repo: Path):
    """A shallow clone fetches origin/dev when it lacks a local dev branch."""
    bare = tmp_path / "remote.git"
    clone = tmp_path / "clone"
    subprocess.run(["git", "clone", "--bare", str(dual_branch_repo), str(bare)], check=True)
    subprocess.run(["git", "clone", "--depth=1", str(bare), str(clone)], check=True)
    # Normal shallow clones have only main; prove the checker fetches dev.
    before = subprocess.run(
        ["git", "-C", str(clone), "show-ref", "--verify", "refs/remotes/origin/dev"],
        capture_output=True,
    )
    assert before.returncode != 0

    checker = GitBranchChecker(clone)
    result = checker.check("leak.go", 5, 11)
    assert "origin/dev" in checker._local_branches
    assert "origin/dev" in result.already_fixed_on


# ── Multi-language pattern tests ─────────────────────────────────────


@pytest.fixture
def python_dual_branch_repo(tmp_path: Path) -> Path:
    """A Python repo where main has a missing close() and dev fixes it."""
    repo = tmp_path / "pyrepo"
    repo.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True, text=True)

    main_src = (
        "def read_file(path):\n"
        "    f = open(path)\n"
        "    data = f.read()\n"
        "    return data\n"
    )
    (repo / "leak.py").write_text(main_src)
    _git("init", "-b", "main")
    _git("config", "user.email", "test@test")
    _git("config", "user.name", "Test")
    _git("add", "leak.py")
    _git("commit", "-m", "initial")

    fixed_src = (
        "def read_file(path):\n"
        "    with open(path) as f:\n"
        "        data = f.read()\n"
        "    return data\n"
    )
    _git("checkout", "-b", "dev")
    (repo / "leak.py").write_text(fixed_src)
    _git("add", "leak.py")
    _git("commit", "-m", "fix: use context manager")
    _git("checkout", "main")
    return repo


def test_python_patterns_detected(python_dual_branch_repo: Path):
    """Python cleanup patterns should be detected cross-branch."""
    checker = GitBranchChecker(python_dual_branch_repo)
    result = checker.check("leak.py", 2, 4)
    assert result.error is None, result.error
    assert "dev" in result.already_fixed_on, f"expected dev, got {result.already_fixed_on}"


# ── Commit pickaxe test ──────────────────────────────────────────────


def test_pickaxe_detects_fix_in_history(tmp_path: Path):
    """A fix commit on the current branch should be detected via pickaxe."""
    repo = tmp_path / "pickaxe-repo"
    repo.mkdir()

    def _git(*args: str) -> None:
        subprocess.run(["git", "-C", str(repo)] + list(args), check=True, capture_output=True, text=True)

    buggy = (
        "package main\n"
        "\n"
        "import \"net\"\n"
        "\n"
        "func process(addr string) error {\n"
        "    conn, err := net.Dial(\"tcp\", addr)\n"
        "    if err != nil {\n"
        "        return err\n"
        "    }\n"
        "    return nil\n"
        "}\n"
    )
    (repo / "server.go").write_text(buggy)
    _git("init", "-b", "main")
    _git("config", "user.email", "test@test")
    _git("config", "user.name", "Test")
    _git("add", "server.go")
    _git("commit", "-m", "initial")

    fixed = buggy.replace("    return nil\n}\n", "    defer conn.Close()\n    return nil\n}\n")
    (repo / "server.go").write_text(fixed)
    _git("add", "server.go")
    _git("commit", "-m", "fix: add defer conn.Close()")

    # Revert to buggy version (simulate scanning old commit).
    (repo / "server.go").write_text(buggy)
    _git("add", "server.go")
    _git("commit", "-m", "revert to buggy for testing")

    checker = GitBranchChecker(repo)
    result = checker.check("server.go", 6, 12)
    # Pickaxe should find the commit that added "defer conn.Close()".
    assert result.fixed_by_commit is not None, "expected pickaxe to find the fix commit"
    assert ".Close()" in (result.fix_evidence or "")


# ── JSON output test ─────────────────────────────────────────────────


def test_to_dict_serialization(dual_branch_repo: Path):
    """BranchCheckResult.to_dict() should produce JSON-serialisable output."""
    import json as _json

    checker = GitBranchChecker(dual_branch_repo)
    result = checker.check("leak.go", 5, 11)
    d = result.to_dict()
    # Verify it's serialisable.
    _json.dumps(d)
    assert d["file_path"] == "leak.go"
    assert d["line_start"] == 5
    assert "dev" in d["already_fixed_on"]
    assert d["function_name"] == "process"
    assert d["fix_evidence"] is not None


# ── Multi-function language detection ────────────────────────────────


def test_patterns_for_file_extensions():
    """Pattern selection should match file extension to language."""
    from smartbench.frontends.git_branch_checker import _patterns_for_file

    go_patterns = _patterns_for_file("main.go")
    py_patterns = _patterns_for_file("app.py")
    js_patterns = _patterns_for_file("server.js")
    ts_patterns = _patterns_for_file("app.ts")
    rs_patterns = _patterns_for_file("lib.rs")
    unknown_patterns = _patterns_for_file("Makefile")

    # Each language should have non-empty pattern list.
    assert len(go_patterns) > 0
    assert len(py_patterns) > 0
    assert len(js_patterns) > 0
    assert len(ts_patterns) > 0
    assert len(rs_patterns) > 0

    # JS and TS should share the same pattern set.
    assert js_patterns is ts_patterns

    # Unknown extensions get all patterns combined.
    assert len(unknown_patterns) >= len(go_patterns) + len(py_patterns)

    # Go patterns should include .Close() detection.
    assert any(p.search(".Close()") for p in go_patterns)

    # Python patterns should include with open().
    assert any(p.search("with open(f)") for p in py_patterns)
