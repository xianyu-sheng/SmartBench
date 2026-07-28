# Terraform MOTD response cleanup benchmark

This case comes from Terraform pull request [#38585](https://github.com/hashicorp/terraform/pull/38585),
which moved `defer resp.Body.Close()` before a body read that could return early.

- before: `820f37b35f4c5d260029e3d5d4688f0ed1e8980a`
- after: `3c94ef65b3487839e5829d6c7d9342da7867cea0`

The snapshots contain the complete historical `internal/command/login.go`. The
invariant requires cleanup registration to dominate the potentially failing read.
