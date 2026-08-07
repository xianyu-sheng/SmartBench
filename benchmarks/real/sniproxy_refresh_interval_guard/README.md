# sniproxy refresh_interval guard

SmartBench-discovered bug in `mosajjal/sniproxy` (PR #203, 2026-08-06):
`ConfigAndStart` for the CIDR ACL did not validate `refresh_interval`
before spawning the refresh goroutine. `time.NewTicker(interval)` panics
on any non-positive duration, and because the panic fires inside the
spawned goroutine it is unrecoverable — the whole process crashes.

Runtime-verified chain: configured `refresh_interval: 0` → reproduced
`panic: non-positive interval for NewTicker` → applied the one-line
validation → same config returns a descriptive error and the worker is
never started.

The `before` snapshot is the vulnerable `ConfigAndStart`; the `after`
snapshot adds the `if d.RefreshInterval <= 0` guard before the worker is
launched. The state rule requires a positive-interval validation branch
to precede the `time.NewTicker` call site.
