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
