# Channel Close Guard

## Vulnerability

**CWE-667**: Improper Locking  
**Severity**: High  
**Category**: Concurrency / Deadlock

## Description

In Go's producer-consumer pattern, the producer must close the channel when done sending data. Failing to close causes consumers using `range` to block forever, resulting in:

1. **Deadlock** - Consumer goroutines block indefinitely
2. **Resource leak** - Blocked goroutines and their stacks never freed
3. **Application hang** - Program appears frozen
4. **Cascading failures** - Other systems waiting on results timeout

## Pattern

### Vulnerable Code (before/main.go)

```go
func GenerateNumbers(max int) <-chan int {
    ch := make(chan int)
    go func() {
        for i := 0; i < max; i++ {
            ch <- i
        }
        // BUG: Channel never closed
    }()
    return ch
}

// Consumer blocks forever after receiving all values
for num := range GenerateNumbers(5) {
    process(num)
}
```

### Fixed Code (after/main.go)

```go
func GenerateNumbers(max int) <-chan int {
    ch := make(chan int)
    go func() {
        defer close(ch) // FIX: Close channel on exit
        for i := 0; i < max; i++ {
            ch <- i
        }
    }()
    return ch
}

// Consumer exits cleanly after channel closes
for num := range GenerateNumbers(5) {
    process(num)
}
```

## State Machine Invariant

**Event**: `make(chan)` creates a channel  
**Guard**: `close(ch)` must be called (usually in defer)  
**Action**: Producer function/goroutine returns  
**Scope**: Intraprocedural

## Real-World Impact

This pattern appears in:
- Stream processing pipelines
- Worker pool task distribution
- Event broadcasting systems
- Batch processing frameworks

### Example Projects

- **etcd**: Watch channel not closed on context cancellation
- **Docker**: Container event stream leaked on client disconnect
- **gRPC**: Server stream not closed on error paths

## Detection

SmartBench detects this pattern by analyzing:
1. Channel creation with `make(chan)`
2. Channel returned or stored in struct field
3. Absence of `close()` call in all producer exit paths
4. Producer goroutine lifecycle

## Common Mistakes

### Mistake 1: Close in wrong goroutine
```go
ch := make(chan int)
go producer(ch)
close(ch) // WRONG: Main goroutine closes before producer finishes
```

### Mistake 2: Close multiple times
```go
close(ch)
close(ch) // PANIC: close of closed channel
```

### Mistake 3: Send after close
```go
close(ch)
ch <- 1 // PANIC: send on closed channel
```

## Best Practices

1. **Producer closes** - The sender, not receiver, closes the channel
2. **Use defer** - `defer close(ch)` ensures closure on all exit paths
3. **Close once** - Only one goroutine should close a channel
4. **Check before close** - Consider using sync.Once if multiple close paths

## References

- [Go Channel Axioms](https://dave.cheney.net/2014/03/19/channel-axioms)
- [Effective Go: Channels](https://go.dev/doc/effective_go#channels)
- CWE-667: Improper Locking
