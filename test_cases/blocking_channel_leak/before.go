// Case 6: Blocking channel send goroutine leak
// Pattern: Goroutine sends to unbuffered channel but receiver exits early
// Impact: Permanent goroutine leak on timeout/error paths

package main

import (
	"errors"
	"fmt"
	"time"
)

// ProcessWithTimeout processes data with a timeout
// BUG: When timeout occurs, the worker goroutine leaks
func ProcessWithTimeout(data string) (string, error) {
	// BUG: Unbuffered channel
	resultChan := make(chan string)

	// Start worker goroutine
	go func() {
		// Simulate expensive processing
		time.Sleep(2 * time.Second)
		result := fmt.Sprintf("processed: %s", data)

		// CRITICAL BUG: This send blocks forever if main goroutine times out
		// The receiver (main goroutine) has already returned, so this send
		// will block forever, leaking this goroutine
		resultChan <- result
	}()

	// Wait for result with timeout
	select {
	case result := <-resultChan:
		return result, nil
	case <-time.After(1 * time.Second):
		// Timeout occurred, return error
		// BUG: Worker goroutine is still running and will block on send
		return "", errors.New("processing timeout")
	}
}

// ParallelFetch fetches data from multiple sources, returns first result
// BUG: All slower goroutines leak because channel is full
func ParallelFetch(urls []string) (string, error) {
	// BUG: Unbuffered channel with multiple senders
	resultChan := make(chan string)

	// Start multiple workers
	for _, url := range urls {
		url := url
		go func() {
			// Simulate fetching
			time.Sleep(time.Duration(len(url)*100) * time.Millisecond)
			result := fmt.Sprintf("fetched: %s", url)

			// BUG: Only first sender succeeds, others block forever
			resultChan <- result
		}()
	}

	// Return first result
	// BUG: Other goroutines still trying to send, they will block forever
	select {
	case result := <-resultChan:
		return result, nil
	case <-time.After(1 * time.Second):
		return "", errors.New("fetch timeout")
	}
}

// ProcessBatch processes items in parallel
// BUG: If error occurs early, remaining goroutines leak
func ProcessBatch(items []string) error {
	errChan := make(chan error, 1) // Only buffered for 1 error

	for _, item := range items {
		item := item
		go func() {
			// Simulate processing
			time.Sleep(500 * time.Millisecond)

			// Simulate random errors
			if len(item)%2 == 0 {
				// BUG: If errChan already has 1 error, this blocks forever
				errChan <- fmt.Errorf("failed to process: %s", item)
			}
		}()
	}

	// Return on first error
	// BUG: Other goroutines still running and may try to send errors
	select {
	case err := <-errChan:
		return err
	case <-time.After(2 * time.Second):
		return nil
	}
}

func main() {
	// Example 1: Timeout leaks goroutine
	fmt.Println("Example 1: ProcessWithTimeout")
	result, err := ProcessWithTimeout("test-data")
	if err != nil {
		fmt.Printf("Error: %v\n", err)
		// LEAK: Worker goroutine still running, blocked on resultChan <- result
	} else {
		fmt.Printf("Result: %s\n", result)
	}

	// Example 2: Multiple senders, one receiver
	fmt.Println("\nExample 2: ParallelFetch")
	urls := []string{"url1", "url2", "url3", "url4", "url5"}
	result, err = ParallelFetch(urls)
	if err != nil {
		fmt.Printf("Error: %v\n", err)
	} else {
		fmt.Printf("First result: %s\n", result)
		// LEAK: 4 goroutines still trying to send to resultChan
	}

	// Example 3: Batch processing with errors
	fmt.Println("\nExample 3: ProcessBatch")
	items := []string{"item1", "item2", "item3", "item4", "item5", "item6"}
	err = ProcessBatch(items)
	if err != nil {
		fmt.Printf("Batch error: %v\n", err)
		// LEAK: Other goroutines may be blocked on errChan
	}

	fmt.Println("\n--- All examples completed, but goroutines leaked ---")
	time.Sleep(100 * time.Millisecond)
}

/*
LEAK ANALYSIS:

Example 1 (ProcessWithTimeout):
- Worker sleeps 2s, timeout is 1s
- Main returns after 1s timeout
- Worker wakes up after 2s, tries to send to resultChan
- No receiver → blocks forever → LEAK

Example 2 (ParallelFetch):
- 5 goroutines started
- First completes and sends to resultChan
- Main receives and returns
- Remaining 4 goroutines still running
- They try to send but no receiver → LEAK

Example 3 (ProcessBatch):
- 6 goroutines started
- errChan buffered size 1
- 3 errors occur (even-length items)
- First error fills buffer
- Main receives and returns
- Other 2 error senders block on full channel → LEAK
*/
