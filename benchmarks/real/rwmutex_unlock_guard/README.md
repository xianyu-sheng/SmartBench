# RWMutex Unlock Guard

## Vulnerability

**CWE-667**: Improper Locking  
**Severity**: High  
**Category**: Concurrency / Deadlock

## Description

`sync.RWMutex` provides read-write locking with two pairs of operations:
- **Read lock**: `RLock()` / `RUnlock()` - Multiple readers can hold simultaneously
- **Write lock**: `Lock()` / `Unlock()` - Exclusive access, blocks all readers and writers

Failing to release locks on all exit paths causes:

1. **Deadlock** - Future lock attempts block forever
2. **Application hang** - All goroutines waiting on the lock freeze
3. **Resource starvation** - Writers blocked by leaked read locks
4. **Silent failure** - No error message, just frozen threads

## Pattern

### Vulnerable Code (before/main.go)

```go
func (c *Cache) Get(key string) (string, error) {
    c.mu.RLock()
    // BUG: No defer c.mu.RUnlock()
    
    if key == "" {
        // Error path leaks the read lock
        return "", fmt.Errorf("empty key")
    }
    
    value := c.data[key]
    c.mu.RUnlock() // Only unlocks on success path
    return value, nil
}
```

**Problem**: Error path returns without calling `RUnlock()`, leaving the read lock held forever.

### Fixed Code (after/main.go)

```go
func (c *Cache) Get(key string) (string, error) {
    c.mu.RLock()
    defer c.mu.RUnlock() // FIX: Unlock on all paths
    
    if key == "" {
        return "", fmt.Errorf("empty key")
    }
    
    value := c.data[key]
    return value, nil
}
```

## State Machine Invariant

### Rule 1: RLock/RUnlock pair
**Event**: `RLock()` called  
**Guard**: `defer RUnlock()` must be called  
**Action**: Function returns  
**Scope**: Intraprocedural

### Rule 2: Lock/Unlock pair
**Event**: `Lock()` called  
**Guard**: `defer Unlock()` must be called  
**Action**: Function returns  
**Scope**: Intraprocedural

## Real-World Impact

This pattern appears in:
- Cache implementations
- Configuration readers
- Concurrent data structures
- Resource pool managers

### Example Projects

- **etcd**: RWMutex not released in watch error paths (etcd#11234)
- **CockroachDB**: SQL parser cache leaked read locks
- **Consul**: Service discovery lock leaked on network errors
- **Vault**: Secret store read lock not released on validation errors

## Detection

SmartBench detects this pattern by analyzing:
1. Calls to `RLock()` or `Lock()`
2. Control flow graph with multiple exit paths
3. Absence of `defer RUnlock()` or `defer Unlock()`
4. Paths that return before unlocking

## Common Mistakes

### Mistake 1: Conditional unlock
```go
c.mu.RLock()
if condition {
    c.mu.RUnlock() // WRONG: Other paths leak
    return
}
process()
c.mu.RUnlock()
```

### Mistake 2: Unlock before all reads
```go
c.mu.RLock()
value := c.data[key]
c.mu.RUnlock()
// WRONG: Still using 'value' which could be invalidated
return processValue(value)
```

### Mistake 3: Mismatched lock types
```go
c.mu.RLock()
defer c.mu.Unlock() // WRONG: Should be RUnlock()
```

### Mistake 4: Lock after defer
```go
defer c.mu.RUnlock() // WRONG: Unlock scheduled before lock
c.mu.RLock()
```

## Best Practices

1. **Always use defer** - `defer mu.Unlock()` immediately after `mu.Lock()`
2. **Match lock types** - RLock→RUnlock, Lock→Unlock
3. **Minimize critical sections** - Lock only what's necessary
4. **Document lock order** - Prevent deadlocks from multiple locks
5. **Consider read vs write** - Use RLock for read-only operations

## Lock Ordering

When multiple locks are needed, always acquire in the same order:

```go
// GOOD: Consistent order
func Transfer(from, to *Account) {
    // Always lock lower ID first
    if from.ID < to.ID {
        from.mu.Lock()
        defer from.mu.Unlock()
        to.mu.Lock()
        defer to.mu.Unlock()
    } else {
        to.mu.Lock()
        defer to.mu.Unlock()
        from.mu.Lock()
        defer from.mu.Unlock()
    }
}
```

## References

- [Go sync.RWMutex documentation](https://pkg.go.dev/sync#RWMutex)
- [The Go Memory Model](https://go.dev/ref/mem)
- [Effective Go: Concurrency](https://go.dev/doc/effective_go#concurrency)
- CWE-667: Improper Locking
