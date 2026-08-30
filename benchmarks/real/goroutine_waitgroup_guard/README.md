# Goroutine WaitGroup Guard

## Vulnerability

**CWE-772**: Missing Release of Resource after Effective Lifetime  
**Severity**: Medium  
**Category**: Concurrency / Goroutine Leak

## Description

When spawning goroutines using `sync.WaitGroup`, the function must call `Wait()` before returning to ensure all goroutines have completed. Failing to wait causes:

1. **Goroutine leak** - Goroutines continue running after function returns
2. **Memory leak** - Goroutine stacks and captured variables remain allocated
3. **Incomplete processing** - Function returns before work is done
4. **Data races** - Goroutines may access freed/invalidated data

## Pattern

### Vulnerable Code (before/main.go)

```go
func ProcessItemsConcurrently(items []string) {
    var wg sync.WaitGroup
    for _, item := range items {
        wg.Add(1)
        go func(s string) {
            defer wg.Done()
            processItem(s)
        }(item)
    }
    // BUG: Returns without wg.Wait()
}
```

### Fixed Code (after/main.go)

```go
func ProcessItemsConcurrently(items []string) {
    var wg sync.WaitGroup
    for _, item := range items {
        wg.Add(1)
        go func(s string) {
            defer wg.Done()
            processItem(s)
        }(item)
    }
    wg.Wait() // FIX: Wait for all goroutines
}
```

## State Machine Invariant

**Event**: `wg.Add()` is called  
**Guard**: `wg.Wait()` must be called  
**Action**: Function returns  
**Scope**: Intraprocedural

## Real-World Impact

This pattern appears in:
- Batch processing systems
- Concurrent API request handlers
- Worker pool implementations
- Test utilities that spawn goroutines

### Example Projects

- **etcd**: Early versions had goroutine leaks in watch systems
- **Kubernetes**: Controller goroutines not properly awaited
- **Prometheus**: Scraper goroutines leaked under error conditions

## Detection

SmartBench detects this pattern by analyzing:
1. Calls to `sync.WaitGroup.Add()`
2. Absence of corresponding `Wait()` call
3. Control flow paths that return without waiting

## References

- [Go sync.WaitGroup documentation](https://pkg.go.dev/sync#WaitGroup)
- [Effective Go: Goroutines](https://go.dev/doc/effective_go#goroutines)
- CWE-772: Missing Release of Resource after Effective Lifetime
