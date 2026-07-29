# Prometheus HTTP response cleanup benchmark

This case comes from Prometheus pull request [#1070](https://github.com/prometheus/prometheus/pull/1070),
“Close HTTP connections on HTTP errors too”. The bug left response bodies open when a
non-200 response or decoder error returned before `defer resp.Body.Close()`.

- before: `66f376f75a4ffb61870f9edd7c021d9740163371`
- after: `9fb65a91af5ce5f0a17931530112c023dbdfd406`

The snapshots contain the complete historical `retrieval/target.go`. The invariant
requires response cleanup to dominate the HTTP-status error return.
