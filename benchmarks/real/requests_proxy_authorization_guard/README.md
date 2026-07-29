# Requests proxy-authorization guard

This benchmark captures the `rebuild_proxies` change that fixed
[GHSA-j8r2-6x86-q33q](https://github.com/advisories/GHSA-j8r2-6x86-q33q)
in `psf/requests`. The vulnerable version writes `Proxy-Authorization` after
extracting credentials without first excluding HTTPS tunnel traffic. The fixed
version adds that state guard.

The fixture is a syntax-complete, scoped excerpt of `requests/sessions.py` from
the pinned parent and fix commits. It tests a security state-transition class,
not resource cleanup, through the Python SemanticIR frontend. The declarative
invariant contains no Python syntax and can be consumed by any frontend that
emits the normalized call, branch, and assignment operations.
