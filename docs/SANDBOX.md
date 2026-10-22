# Sandbox backends and threat model

Dynamic verification means **executing repository test code**. The sandbox
verifier is opt-in (`smartbench quick --sandbox`) and has two backends with
different guarantees.

## Backends

| | `local` (default) | `container` |
|---|---|---|
| Working tree protection | temporary copy | temporary copy + bind mount |
| Filesystem | host, current user | read-only root; workspace rw; tmpfs `/tmp` |
| Network | host network | `--network none` |
| User | current user | non-root (`--user uid:gid` on POSIX) |
| Capabilities | host | `--cap-drop ALL`, `no-new-privileges` |
| Resource limits | timeout only | timeout, memory, CPU, PID limits, tmpfs size |
| Host env | credential-like names filtered | explicit allowlist only (`CI`, `HOME=/tmp`, …) |
| Kernel isolation | none | shares the host kernel |

Use the container backend when the repository is not fully trusted:

```bash
smartbench quick --project ./repo --sandbox \
  --sandbox-backend container \
  --sandbox-image golang:1.22 \
  --sandbox-runtime podman
```

- The **image must contain the project toolchain** (`git`, compiler, test
  runner). The default `python:3.12-slim` is only a starting point.
- The runtime is auto-detected (`podman` first, then `docker`); a missing
  runtime is a clear opt-in error, not a silent fallback.
- Rootless podman is recommended. Docker usually needs a root daemon.

## Threat model

The verifier assumes the repository and its tests may be hostile:

- **Exfiltration**: no host environment, no network, no home-directory mount.
  Only the copied workspace is visible.
- **Filesystem modification**: the original checkout is never mounted; only the
  temporary copy is writable inside the container.
- **Resource abuse**: memory/CPU/PID limits, tmpfs cap, bounded copy size and a
  wall-clock timeout. A fork bomb hits `--pids-limit`.
- **Credential leakage**: host variables are not forwarded; only the allowlist
  above is set.

Out of scope:

- Container escapes / host-kernel vulnerabilities. This is **not** a microVM.
- Side channels and runtime daemon compromise.
- Toolchain supply-chain attacks inside the chosen image.
- The workspace mount is writable by the container; the copy is deleted after
  the run, but the tests can still modify their own copy.

## CI and tests

- Unit tests construct the container command and verify flags/env without a
  runtime; they run everywhere.
- Container integration tests are opt-in:

```bash
SMARTBENCH_TEST_CONTAINER=1 SMARTBENCH_TEST_IMAGE=python:3.12-slim \
  python -m pytest tests/test_sandbox_backends.py -q
```

They skip when the runtime or image is absent — CI never pulls or starts a
container by default.
