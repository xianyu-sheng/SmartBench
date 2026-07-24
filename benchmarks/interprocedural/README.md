# Interprocedural guard benchmark

This small Python benchmark is an architectural regression fixture, not a
claim about a particular upstream repository.  The event is observed in a
caller, the retry action is performed in a callee, and the fixed snapshot adds
a callee-side branch that controls the retry path.

Run it with:

```bash
smartbench benchmark run --manifest benchmarks/interprocedural/manifest.yaml
```
