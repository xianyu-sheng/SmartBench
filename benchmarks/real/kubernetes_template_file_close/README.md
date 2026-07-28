# Kubernetes template file cleanup benchmark

This case comes from Kubernetes pull request [#29495](https://github.com/kubernetes/kubernetes/pull/29495),
which fixed a file descriptor leak in `resource_printer.go` by deferring `file.Close()`.

- before: `0e8d51522586a5babaf96df3a185a5ed1d0967c5`
- after: `236a22506083f5d18122a3a8d4ea42e7b1cdae98`

The snapshots contain the complete historical source file. This case also exercises
Go `switch` case control flow and assignment initializers inside `if` statements.
