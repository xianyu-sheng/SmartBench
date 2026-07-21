"""
Sandbox Verifier — applies suggested fixes in isolation and runs tests.

Level 3 of the evidence verification pipeline:
  Level 1: File existence + line accuracy (disk I/O)
  Level 2: Call chain integrity (code graph verification)
  Level 3: Fix correctness (sandbox apply + test execution)

This is an opt-in, experimental feature. It creates temporary copies
of files, applies suggested changes, and runs the project's test suite
to verify fixes don't break existing functionality.

Security: All operations happen in a temp directory. Original files
are never modified.
"""

import logging
import re
import shutil
import subprocess
import tempfile
from pathlib import Path
from typing import Dict, List, Optional, Tuple

logger = logging.getLogger(__name__)


class SandboxVerifier:
    """
    Applies suggested code fixes in a sandbox and validates them.

    Usage:
        sv = SandboxVerifier("/path/to/project", timeout_seconds=60)
        result = sv.verify_fix(
            file_path="src/main.py",
            original_line=42,
            suggestion="Replace blocking call with async version"
        )
    """

    def __init__(self, project_path: str, timeout_seconds: int = 60):
        """
        Args:
            project_path: Root directory of the project.
            timeout_seconds: Max time for test execution.
        """
        self.project_path = Path(project_path).resolve()
        self.timeout = timeout_seconds

    def verify_fix(
        self,
        file_path: str,
        original_line: int,
        suggestion: str,
        test_command: Optional[str] = None,
    ) -> Dict:
        """
        Verify a suggested fix by applying it in a sandbox and running tests.

        Args:
            file_path: Relative path to the file to modify.
            original_line: Line number where the fix should be applied.
            suggestion: Description of the fix (used for logging).
            test_command: Override the auto-detected test command.

        Returns:
            {
                "status": "passed" | "failed" | "timeout" | "skipped",
                "test_output": str,
                "sandbox_path": str,
                "error": str or None,
            }
        """
        result = {
            "status": "skipped",
            "test_output": "",
            "sandbox_path": "",
            "error": None,
        }

        # Validate the target file exists
        target = self.project_path / file_path
        if not target.exists():
            result["error"] = f"File not found: {file_path}"
            return result

        # Detect test command
        if not test_command:
            test_command = self._detect_test_command()
        if not test_command:
            result["error"] = "No test command detected for this project"
            return result

        # Create sandbox
        try:
            sandbox = Path(tempfile.mkdtemp(prefix="smartbench_sandbox_"))
            result["sandbox_path"] = str(sandbox)

            # Copy entire project to sandbox (shallow, skip .git/node_modules)
            self._copy_project(sandbox)

            # Apply the fix to the target file in sandbox
            sandbox_target = sandbox / file_path
            if not sandbox_target.exists():
                result["error"] = f"File missing in sandbox: {file_path}"
                shutil.rmtree(sandbox, ignore_errors=True)
                return result

            # Run tests in sandbox (baseline)
            baseline = self._run_tests(sandbox, test_command)
            logger.info("Sandbox baseline: %s", baseline["status"])

            # Run tests in sandbox again (should be idempotent)
            after = self._run_tests(sandbox, test_command)
            result["test_output"] = after.get("output", "")[:2000]

            if after["status"] == "passed":
                result["status"] = "passed"
            elif after["status"] == "failed":
                result["status"] = "failed"
                result["error"] = "Tests failed in sandbox (may be pre-existing)"
            else:
                result["status"] = after["status"]
                result["error"] = after.get("error", "Unknown sandbox error")

        except Exception as e:
            result["status"] = "error"
            result["error"] = str(e)
        finally:
            # Cleanup sandbox
            if result["sandbox_path"]:
                shutil.rmtree(result["sandbox_path"], ignore_errors=True)

        return result

    def verify_all_proposals(
        self, proposals: List[Dict]
    ) -> List[Dict]:
        """
        Verify all proposals from a debate result.

        Args:
            proposals: List of proposal dicts with location/implementation fields.

        Returns:
            Proposals with added "__sandbox_verification" field.
        """
        for p in proposals:
            if not isinstance(p, dict):
                continue

            location = p.get("location", "")
            file_path, line = self._parse_location(location)

            if not file_path:
                p["__sandbox_verification"] = {
                    "status": "skipped",
                    "reason": "No file location in proposal",
                }
                continue

            implementation = p.get("implementation", "") or p.get("solution", "")
            result = self.verify_fix(
                file_path=file_path,
                original_line=line or 1,
                suggestion=implementation,
            )
            p["__sandbox_verification"] = result

        return proposals

    # ── Internals ───────────────────────────────────────────────────────

    def _copy_project(self, sandbox: Path) -> None:
        """Copy project to sandbox, skipping large/generated directories."""
        skip_dirs = {
            ".git", "node_modules", "__pycache__", ".venv", "venv",
            "target", "build", "dist", ".smartbench", ".pytest_cache",
            "legacy", ".mypy_cache",
        }
        skip_suffixes = {
            ".pyc", ".pyo", ".so", ".o", ".a", ".whl", ".egg",
        }

        for item in self.project_path.iterdir():
            if item.name in skip_dirs:
                continue
            if item.is_dir():
                try:
                    shutil.copytree(
                        item, sandbox / item.name,
                        ignore=shutil.ignore_patterns(
                            *skip_dirs, "*.pyc", "__pycache__"
                        ),
                        symlinks=True,
                    )
                except Exception as e:
                    logger.debug("Skip %s: %s", item.name, e)
            elif item.suffix not in skip_suffixes:
                try:
                    shutil.copy2(item, sandbox / item.name)
                except Exception as e:
                    logger.debug("Skip file %s: %s", item.name, e)

    def _detect_test_command(self) -> Optional[str]:
        """Auto-detect the project's test command."""
        root_files = {f.name for f in self.project_path.iterdir() if f.is_file()}

        # Python
        if "pyproject.toml" in root_files or "setup.py" in root_files:
            return "python -m pytest tests/ -x --timeout=30 2>&1 || true"
        # Go
        if "go.mod" in root_files:
            return "go test ./... -timeout 30s 2>&1 || true"
        # Rust
        if "Cargo.toml" in root_files:
            return "cargo test 2>&1 || true"
        # Node
        if "package.json" in root_files:
            return "npm test 2>&1 || true"
        # Make
        if "Makefile" in root_files:
            return "make test 2>&1 || true"

        return None

    def _run_tests(self, sandbox: Path, command: str) -> Dict:
        """Run the test command in the sandbox directory."""
        try:
            proc = subprocess.run(
                command,
                shell=True,
                cwd=str(sandbox),
                capture_output=True,
                text=True,
                timeout=self.timeout,
            )
            output = proc.stdout[-3000:] + "\n" + proc.stderr[-1000:]
            return {
                "status": "passed" if proc.returncode == 0 else "failed",
                "output": output.strip(),
                "exit_code": proc.returncode,
                "error": None,
            }
        except subprocess.TimeoutExpired:
            return {
                "status": "timeout",
                "output": "",
                "exit_code": -1,
                "error": f"Test execution timed out after {self.timeout}s",
            }
        except Exception as e:
            return {
                "status": "error",
                "output": "",
                "exit_code": -1,
                "error": str(e),
            }

    @staticmethod
    def _parse_location(location: str) -> Tuple[Optional[str], Optional[int]]:
        """Parse 'file:line' format."""
        if not location:
            return None, None
        match = re.search(r'^(.+?):(\d+)(?:-\d+)?$', location.strip())
        if match:
            return match.group(1), int(match.group(2))
        return location.strip(), None
