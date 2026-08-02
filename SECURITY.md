# Security Policy

## Reporting a vulnerability

Please do not open a public issue.

Report privately through
[GitHub Security Advisories](https://github.com/xianyu-sheng/SmartBench/security/advisories/new).
Include the affected version, reproduction steps, and the impact you believe it
has.

SmartBench is a public beta maintained by one person, so please allow reasonable
time for a response before any disclosure.

## Supported versions

Only the latest release on `main` receives fixes.

## What SmartBench does with your code

This matters more than usual for a tool like this, so it is stated plainly.

**The deterministic path is local and read-only.** `smartbench unified run`
analyzes files on disk. It does not edit the analyzed repository, open issues or
pull requests, or contact a maintainer.

**The `quick` path sends repository content to a remote model provider.** If you
configure a provider key, source snippets, file paths, and derived facts are
transmitted to that provider and are subject to their retention policy. Do not
run `quick` against a repository containing secrets or restricted source unless
that exposure is acceptable.

**Reports do not persist secrets by design.** Experiment runners record provider
and model names plus parsed decision statistics; API keys, raw prompts, and raw
model responses are not written to reports. If you find a path where a key or raw
prompt reaches a report file or log, please treat that as a security issue and
report it privately.

**Path handling is confined to the repository root.** External symlinks and `../`
escapes are ignored. External commands run without a shell and have time and
output bounds.

**`quick --sandbox` is not a security boundary.** It applies proposed patches to a
temporary copy, but repository tests still execute with the current user's OS
permissions. Do not use it to run untrusted code.

## Using SmartBench findings

Do not file a SmartBench finding upstream without repeated verification and a
human decision. A `verified` label refers to source-location and structural
checks, not to whether a bug conclusion is correct.
