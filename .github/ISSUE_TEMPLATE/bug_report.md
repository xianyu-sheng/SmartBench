---
name: Bug report
about: Something behaves incorrectly
title: ''
labels: bug
assignees: ''
---

## What happened

## What you expected

## Minimal reproduction

A small fixture that triggers the behaviour is the most useful thing you can
provide. A synthetic file is fine if the real repository cannot be shared.

```
# the exact command you ran
```

```python
# the smallest source file that reproduces it
```

## Environment

- SmartBench version (`smartbench --version`):
- Python version:
- Extras installed (`graph`, `rag`, `dev`):
- OS:

## Report output

If the JSON report is relevant, paste the significant part — especially
`errors`, `stats`, and `analysis_status` for the rule involved.

<details>
<summary>Report excerpt</summary>

```json

```

</details>

## Notes

If this involves a finding you believe is wrong, please say whether you think it
is a false positive (reported but not a real bug) or a false negative (real bug
not reported). A clean report can legitimately mean "no supported finding" rather
than "no bugs" — see the capability status in `analysis_status`.
