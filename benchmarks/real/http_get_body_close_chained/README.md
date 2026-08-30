# HTTP Get Body Close - Chained Call Pattern

## Vulnerability

**CWE-772**: Missing Release of Resource after Effective Lifetime  
**Severity**: High  
**Category**: Resource Leak / HTTP Connection Pool Exhaustion

## Description

This case demonstrates a common pattern where `http.Get()` is used directly in a chained expression, making it easy to forget closing the response body. This leads to:

1. **Connection pool exhaustion** - Default pool has limited connections
2. **File descriptor leak** - Each unclosed body holds a socket
3. **Application failure** - After pool exhaustion, new requests hang or fail
4. **Memory leak** - Response bodies and buffers remain allocated

## Pattern

### Vulnerable Code (before/main.go)

```go
// BUG: Chained call loses the response reference
body, err := io.ReadAll(http.Get(url))
```

The problem:
- `http.Get(url)` returns `(*Response, error)`
- We immediately call `.Body` and pass it to `ReadAll`
- We never store the `Response` object, so we can't call `Close()`
- The connection remains open indefinitely

### Fixed Code (after/main.go)

```go
// FIX: Store response and close body
resp, err := http.Get(url)
if err != nil {
    return "", err
}
defer resp.Body.Close()

body, err := io.ReadAll(resp.Body)
```

## Type Checker Enhancement

### Before Enhancement

The original type checker **rejected** expressions with parentheses:

```python
def _probeable_receiver(receiver: str) -> bool:
    if "(" in stripped or ")" in stripped:
        return False  # ❌ http.Get(url).Body was skipped
```

Result: This vulnerability pattern was **not detected**.

### After Enhancement

The enhanced type checker **accepts** single-call patterns:

```python
def _probeable_receiver(receiver: str) -> bool:
    # Support: http.Get(url).Body
    if _is_single_call_pattern(expr):
        return True  # ✅ Now properly detected
```

Algorithm:
1. Parse expression to detect single function call
2. Verify field access after the call: `.Body`
3. Pass to typeprobe for type resolution
4. Detect that `Body` needs to be closed

## Real-World Impact

This pattern appears frequently in:
- Quick HTTP fetch utilities
- One-liner API calls
- Example code snippets
- Prototype code that makes it to production

### Example Projects

- **Kubernetes**: Early versions had similar patterns in API clients
- **Prometheus**: HTTP client code had inline Get() calls
- **etcd**: Discovery service used chained HTTP calls
- **Docker**: Registry client leaked connections on errors

### Connection Pool Limits

Go's default HTTP client has **MaxIdleConnsPerHost = 2**:
```go
// After 2 leaked connections, new requests hang
for i := 0; i < 10; i++ {
    io.ReadAll(http.Get(url))  // Leaks connection
}
// Connection 3+ will block waiting for available slot
```

## Detection Strategy

SmartBench detects this through enhanced type analysis:

1. **Parse chained call**: `http.Get(url).Body`
2. **Type resolution**: Typeprobe identifies `Body` as `io.ReadCloser`
3. **Resource protocol**: `io.ReadCloser` requires `Close()`
4. **Flow analysis**: No `defer Close()` found before read
5. **Report finding**: Resource leak detected

## Common Mistakes

### Mistake 1: Inline expression
```go
// WRONG: Can't close what you don't store
body, _ := io.ReadAll(http.Get(url))
```

### Mistake 2: Ignoring error
```go
// WRONG: Get can fail, and resp is nil
resp, _ := http.Get(url)
defer resp.Body.Close() // Panics if resp is nil!
```

### Mistake 3: Not checking status
```go
// WRONG: 404 response still needs Body closed
resp, _ := http.Get(url)
defer resp.Body.Close()
// Should check resp.StatusCode
```

### Mistake 4: Closing before read
```go
// WRONG: Close too early
resp, _ := http.Get(url)
resp.Body.Close()
body, _ := io.ReadAll(resp.Body) // Reads from closed body
```

## Best Practices

### 1. Always store the response
```go
resp, err := http.Get(url)
if err != nil {
    return err
}
defer resp.Body.Close()
```

### 2. Handle errors properly
```go
resp, err := http.Get(url)
if err != nil {
    return fmt.Errorf("get failed: %w", err)
}
defer resp.Body.Close()

if resp.StatusCode != 200 {
    return fmt.Errorf("bad status: %d", resp.StatusCode)
}
```

### 3. Drain body on error
```go
resp, err := http.Get(url)
if err != nil {
    return err
}
defer resp.Body.Close()

if resp.StatusCode != 200 {
    // Drain and close allows connection reuse
    io.Copy(io.Discard, resp.Body)
    return fmt.Errorf("status: %d", resp.StatusCode)
}
```

### 4. Use helper functions
```go
func fetch(url string) ([]byte, error) {
    resp, err := http.Get(url)
    if err != nil {
        return nil, err
    }
    defer resp.Body.Close()
    
    return io.ReadAll(resp.Body)
}
```

## Performance Impact

### Before Fix (Leaked Connections)
- Request 1-2: Fast (uses pool)
- Request 3+: **Slow** (creates new connections)
- Request 6+: **Very slow** (waits for timeout)
- Eventually: **Application hang**

### After Fix (Proper Cleanup)
- All requests: Fast (connection reuse)
- Stable memory usage
- Predictable performance

## References

- [Go HTTP Client Best Practices](https://pkg.go.dev/net/http#Client)
- [Don't forget to close your response body](https://www.joeshaw.org/dont-defer-close-on-writable-files/)
- [HTTP connection pooling](https://cs.opensource.google/go/go/+/refs/tags/go1.21.0:src/net/http/transport.go)
- CWE-772: Missing Release of Resource after Effective Lifetime
