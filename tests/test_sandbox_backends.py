"""Tests for sandbox backends (local + container)."""

import os
import subprocess
import sys
from pathlib import Path
from types import SimpleNamespace

import pytest

from smartbench.verifier import sandbox_backends as backends
from smartbench.verifier.sandbox import SandboxVerifier
from smartbench.verifier.sandbox_backends import (
    ContainerBackend,
    LocalTempBackend,
    SandboxBackendError,
    make_backend,
)


def test_local_backend_filters_credentials(tmp_path, monkeypatch):
    monkeypatch.setenv("SMARTBENCH_TEST_SECRET", "must-not-leak")
    monkeypatch.setenv("SMARTBENCH_SAFE_MARKER", "safe-value")

    result = LocalTempBackend().run(
        [
            sys.executable,
            "-c",
            (
                "import os; "
                "print(os.getenv('SMARTBENCH_TEST_SECRET')); "
                "print(os.getenv('SMARTBENCH_SAFE_MARKER')); "
                "print(os.getenv('SMARTBENCH_SANDBOX'))"
            ),
        ],
        tmp_path,
        timeout=10,
    )

    assert result.status == "passed"
    assert "must-not-leak" not in result.output
    assert "safe-value" in result.output
    assert result.output.endswith("1")


def test_container_command_is_locked_down(tmp_path, monkeypatch):
    monkeypatch.setattr(
        backends.shutil,
        "which",
        lambda name: "/usr/bin/podman" if name == "podman" else None,
    )
    captured = {}

    def fake_run_bounded(args, **kwargs):
        captured["args"] = args
        captured["kwargs"] = kwargs
        return SimpleNamespace(returncode=0, stdout="ok", stderr="")

    monkeypatch.setattr(backends, "run_bounded", fake_run_bounded)
    backend = ContainerBackend(
        image="example:test",
        memory="512m",
        cpus="1.5",
        pids_limit=128,
        tmpfs_size="32m",
    )

    result = backend.run(["sh", "-c", "true"], tmp_path, timeout=7)

    args = captured["args"]
    assert result.status == "passed"
    assert args[:2] == ["/usr/bin/podman", "run"]
    assert "--rm" in args
    assert args[args.index("--network") + 1] == "none"
    assert "--read-only" in args
    assert args[args.index("--cap-drop") + 1] == "ALL"
    assert args[args.index("--pids-limit") + 1] == "128"
    assert args[args.index("--memory") + 1] == "512m"
    assert args[args.index("--cpus") + 1] == "1.5"
    assert args[args.index("--tmpfs") + 1] == "/tmp:rw,nosuid,nodev,size=32m"
    assert args[args.index("-w") + 1] == "/workspace"
    assert f"{tmp_path.resolve()}:/workspace:rw" in args
    assert args[-4:] == ["example:test", "sh", "-c", "true"]
    if hasattr(os, "getuid"):
        assert "--user" in args
    env_values = [args[index + 1] for index, arg in enumerate(args) if arg == "--env"]
    assert env_values == [
        "CI=1",
        "HOME=/tmp",
        "GIT_TERMINAL_PROMPT=0",
        "SMARTBENCH_SANDBOX=1",
    ]
    assert not any("SECRET" in value for value in env_values)
    assert "--env" in args


def test_container_stdin_flag(tmp_path, monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: "/usr/bin/docker")
    captured = {}

    def fake_run_bounded(args, **kwargs):
        captured["args"] = args
        return SimpleNamespace(returncode=0, stdout="", stderr="")

    monkeypatch.setattr(backends, "run_bounded", fake_run_bounded)
    backend = ContainerBackend(image="example:test")

    backend.run(["sh", "-c", "cat"], tmp_path, timeout=5, input_text="hello")

    assert "-i" in captured["args"]


def test_missing_runtime_is_a_clear_error(monkeypatch):
    monkeypatch.setattr(backends.shutil, "which", lambda name: None)

    with pytest.raises(SandboxBackendError, match="requires a runtime"):
        ContainerBackend()


def test_make_backend_rejects_unknown_name():
    with pytest.raises(SandboxBackendError, match="unknown sandbox backend"):
        make_backend("microvm")
    with pytest.raises(ValueError, match="unknown sandbox backend"):
        SandboxVerifier(str(Path.cwd()), backend="microvm")


def _container_runtime():
    for name in ("podman", "docker"):
        if backends.shutil.which(name):
            return name
    return None


@pytest.mark.skipif(
    os.environ.get("SMARTBENCH_TEST_CONTAINER") != "1",
    reason="set SMARTBENCH_TEST_CONTAINER=1 to run container integration tests",
)
def test_container_blocks_network_and_host_env(tmp_path, monkeypatch):
    runtime = _container_runtime()
    if runtime is None:
        pytest.skip("no container runtime")
    image = os.environ.get("SMARTBENCH_TEST_IMAGE", "python:3.12-slim")
    probe = subprocess.run(
        [runtime, "image", "inspect", image],
        capture_output=True,
        text=True,
    )
    if probe.returncode != 0:
        pytest.skip(f"container image not present: {image}")

    monkeypatch.setenv("SMARTBENCH_TEST_SECRET", "must-not-leak")
    backend = ContainerBackend(image=image, runtime=runtime)
    result = backend.run(
        [
            "python",
            "-c",
            (
                "import os, socket\n"
                "print('SECRET', os.getenv('SMARTBENCH_TEST_SECRET'))\n"
                "try:\n"
                "    socket.create_connection(('1.1.1.1', 53), 2)\n"
                "    print('NETWORK_OPEN')\n"
                "except OSError:\n"
                "    print('NETWORK_BLOCKED')\n"
            ),
        ],
        tmp_path,
        timeout=30,
    )

    assert result.status == "passed", result.error
    assert "must-not-leak" not in result.output
    assert "NETWORK_BLOCKED" in result.output
