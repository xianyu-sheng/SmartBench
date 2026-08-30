// Case 6: Blocking channel send goroutine leak - FIXED
// Pattern: Use buffered channels or context cancellation
// Fix: Prevent goroutine blocking on channel send

package main

import (
	"context"
	"errors"
	"fmt"
	"time"
)

// ProcessWithTimeout processes data with a timeout
// FIXED: Use buffered channel to prevent goroutine leak
func ProcessWithTimeout(data string) (string, error) {
	// FIX: Buffered channel with size 1
	// Worker can send even if main goroutine times out
	resultChan := make(chan string, 1)

	// Start worker goroutine
	go func() {
		// Simulate expensive processing
		time.Sleep(2 * time.Second)
		result := fmt.Sprintf("processed: %s", data)

		// FIX: Send won't block because channel has buffer
		// If main already returned, this write succeeds but value is discarded
		resultChan <- result
		// Goroutine exits cleanly
	}()

	// Wait for result with timeout
	select {
	case result := <-resultChan:
		return result, nil
	case <-time.After(1 * time.Second):
		return "", errors.New("processing timeout")
		// Worker can still send and exit, no leak
	}
}

// ProcessWithTimeoutCtx uses context for cancellation
// FIXED: Worker checks context before sending
func ProcessWithTimeoutCtx(ctx context.Context, data string) (string, error) {
	resultChan := make(chan string)

	// Start worker goroutine
	go func() {
		// Simulate expensive processing
		time.Sleep(2 * time.Second)
		result := fmt.Sprintf("processed: %s", data)

		// FIX: Check context before sending
		select {
		case resultChan <- result:
			// Sent successfully
		case <-ctx.Done():
			// Context canceled, exit without sending
			return
		}
	}()

	// Wait for result with timeout
	select {
	case result := <-resultChan:
		return result, nil
	case <-ctx.Done():
		return "", ctx.Err()
	}
}

// ParallelFetch fetches data from multiple sources, returns first result
// FIXED: Use buffered channel to hold all results
func ParallelFetch(urls []string) (string, error) {
	// FIX: Buffered channel sized for all workers
	resultChan := make(chan string, len(urls))

	// Start multiple workers
	for _, url := range urls {
		url := url
		go func() {
			// Simulate fetching
			time.Sleep(time.Duration(len(url)*100) * time.Millisecond)
			result := fmt.Sprintf("fetched: %s", url)

			// FIX: All sends succeed because channel has enough buffer
			resultChan <- result
			// All goroutines exit cleanly
		}()
	}

	// Return first result
	select {
	case result := <-resultChan:
		return result, nil
	case <-time.After(1 * time.Second):
		return "", errors.New("fetch timeout")
	}
	// Remaining results stay in channel but goroutines exit
}

// ParallelFetchCtx uses context for explicit cancellation
// FIXED: All workers respect context cancellation
func ParallelFetchCtx(ctx context.Context, urls []string) (string, error) {
	resultChan := make(chan string)

	// Start multiple workers
	for _, url := range urls {
		url := url
		go func() {
			// Simulate fetching
			time.Sleep(time.Duration(len(url)*100) * time.Millisecond)
			result := fmt.Sprintf("fetched: %s", url)

			// FIX: Check context before sending
			select {
			case resultChan <- result:
			case <-ctx.Done():
				return
			}
		}()
	}

	// Return first result
	select {
	case result := <-resultChan:
		return result, nil
	case <-ctx.Done():
		return "", ctx.Err()
	}
}

// ProcessBatch processes items in parallel
// FIXED: Use buffered channel for all potential errors
func ProcessBatch(items []string) error {
	// FIX: Buffer size = number of workers
	// All errors can be sent without blocking
	errChan := make(chan error, len(items))

	for _, item := range items {
		item := item
		go func() {
			// Simulate processing
			time.Sleep(500 * time.Millisecond)

			// Simulate random errors
			if len(item)%2 == 0 {
				// FIX: Send won't block because channel has enough buffer
				errChan <- fmt.Errorf("failed to process: %s", item)
			}
		}()
	}

	// Return on first error
	select {
	case err := <-errChan:
		return err
	case <-time.After(2 * time.Second):
		return nil
	}
	// All goroutines exit cleanly, errors remain in channel
}

// ProcessBatchCtx uses context for coordination
// FIXED: Workers can be canceled
func ProcessBatchCtx(ctx context.Context, items []string) error {
	errChan := make(chan error, 1)

	for _, item := range items {
		item := item
		go func() {
			// Check cancellation early
			select {
			case <-ctx.Done():
				return
			default:
			}

			// Simulate processing
			time.Sleep(500 * time.Millisecond)

			if len(item)%2 == 0 {
				select {
				case errChan <- fmt.Errorf("failed to process: %s", item):
				case <-ctx.Done():
					return
				}
			}
		}()
	}

	select {
	case err := <-errChan:
		return err
	case <-ctx.Done():
		return ctx.Err()
	}
}

func main() {
	// Example 1: Buffered channel solution
	fmt.Println("Example 1: ProcessWithTimeout (buffered)")
	result, err := ProcessWithTimeout("test-data")
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		// NO LEAK: Worker can send to buffered channel and exit
	} else {
		fmt.Printf("Result: %s\n", result)
	}

	// Example 2: Context-based solution
	fmt.Println("\nExample 2: ProcessWithTimeoutCtx (context)")
	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
	result, err = ProcessWithTimeoutCtx(ctx, "test-data")
	cancel()
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		// NO LEAK: Worker checks context and exits
	}

	// Example 3: Multiple senders with buffered channel
	fmt.Println("\nExample 3: ParallelFetch (buffered)")
	urls := []string{"url1", "url2", "url3", "url4", "url5"}
	result, err = ParallelFetch(urls)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
	} else {
		fmt.Printf("First result: %s\n", result)
		// NO LEAK: All workers send to buffered channel and exit
	}

	// Example 4: Batch processing with proper buffer size
	fmt.Println("\nExample 4: ProcessBatch (buffered)")
	items := []string{"item1", "item2", "item3", "item4", "item5", "item6"}
	err = ProcessBatch(items)
	if err != nil {
		fmt.Printf("Batch error: %v\n", err)
		// NO LEAK: All error sends succeed
	}

	fmt.Println("\n--- All examples completed without leaks ---")
	time.Sleep(100 * time.Millisecond)
}

/*
FIX STRATEGIES:

1. Buffered Channel:
   - Size channel to hold all potential sends
   - Workers can send and exit even if no receiver
   - Trade-off: Memory for N buffered items

2. Context Cancellation:
   - Pass context to workers
   - Workers check ctx.Done() before sending
   - Explicit coordination and cleanup

3. Hybrid:
   - Small buffer (1-10) for common case
   - Context for explicit cancellation
   - Best of both worlds

GENERAL RULES:
- Never send to unbuffered channel in goroutine with timeout
- Use context for long-lived operations
- Buffer size >= number of concurrent senders
- Always provide goroutine exit path
*/
