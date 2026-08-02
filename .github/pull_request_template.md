## Summary

<!-- What changes and why. Link the issue: "Closes #N" -->

## Type of change

- [ ] Bug fix
- [ ] New capability
- [ ] Refactor (no behaviour change)
- [ ] Documentation
- [ ] Benchmark / test infrastructure

## Verification

```
ruff check smartbench tests
pytest -q
```

- Tests before: <!-- N passed -->
- Tests after: <!-- N passed -->

<!-- If you added a test, say what it would catch. If a behaviour changed, say
     what a user would observe differently. -->

## Evidence boundary

- [ ] No hypothesis can reach the `fact-*` namespace
- [ ] No language parser is imported from a language-neutral analyzer
- [ ] Capabilities claimed `full` have a passing behavioural assertion
- [ ] Unprovable results stay `unknown` / `partial` / `abstained` rather than
      being omitted or upgraded

<!-- Delete any line that does not apply to this change. -->

## Backward compatibility

<!-- Does any existing command, flag, report field, or exit code change?
     If a new flag was added, state its default and confirm the default
     preserves current behaviour. -->
