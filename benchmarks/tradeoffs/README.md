# Engineering trade-off fixtures

These fixtures exercise the counterfactual retry-loop analyzer
(`retry_without_backoff`). They model a change that is *correct by the letter*
of the reported defect but *worse under the condition that triggers it* — the
case where a reviewer should compare the patched behaviour against the status
quo before filing upstream.

## `lf_accept_retry`

Source: [gokcehan/lf#2712](https://github.com/gokcehan/lf/pull/2712) — a patch
that stopped the server from handing a nil connection to `handleConn` by
`continue`-ing on an `Accept` error. Maintainers rejected the change as a
trade-off: under file-descriptor exhaustion (`EMFILE`) the immediate retry
becomes a tight accept loop that burns a core and floods logs, which they
considered worse than the crash.

| Snapshot | Shape | Expected |
|---|---|---|
| `before` | error path falls through to `handleConn` (fails closed) | no finding |
| `busy_loop` | error path logs and `continue`s with no backoff or bound | 1 finding |
| `with_backoff` | error path sleeps before retrying | no finding |

A retry that is bounded (attempt counter guard) is likewise not reported; the
analyzer only claims the deterministic shape “error outcome reaches a
loop-back edge without a mitigation or an exit”.
