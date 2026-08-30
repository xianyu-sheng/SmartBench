// Case 4: etcd/Kubernetes context cancellation goroutine leak - FIXED
// Source: https://github.com/kubernetes/kubernetes/pull/25331
// Pattern: Proper goroutine termination on context cancellation
// Fix: Use context.Done() to signal goroutine termination

package main

import (
	"context"
	"time"
)

type Watcher struct {
	resultChan chan WatchResponse
	ctx        context.Context
}

type WatchResponse struct {
	Events []Event
}

type Event struct {
	Key   string
	Value string
}

// NewWatcher creates a new etcd watcher
// FIXED: Goroutine properly exits when context is canceled
func NewWatcher(ctx context.Context, key string) *Watcher {
	w := &Watcher{
		resultChan: make(chan WatchResponse, 10),
		ctx:        ctx,
	}

	// Start background goroutine to watch for events
	go func() {
		// FIX: Check context.Done() to exit gracefully
		for {
			select {
			case <-ctx.Done():
				// Context canceled, exit goroutine immediately
				close(w.resultChan)
				return
			default:
				// Simulate receiving watch events
				resp := WatchResponse{
					Events: []Event{{Key: key, Value: "value"}},
				}

				// FIX: Use select with context to avoid blocking forever
				select {
				case w.resultChan <- resp:
					// Event sent successfully
				case <-ctx.Done():
					// Context canceled during send, exit
					close(w.resultChan)
					return
				}

				time.Sleep(100 * time.Millisecond)
			}
		}
	}()

	return w
}

// ResultChan returns the channel for receiving watch results
func (w *Watcher) ResultChan() <-chan WatchResponse {
	return w.resultChan
}

func main() {
	ctx, cancel := context.WithTimeout(context.Background(), 1*time.Second)
	defer cancel()

	watcher := NewWatcher(ctx, "/config/key")

	// Read a few events
	for i := 0; i < 3; i++ {
		select {
		case resp := <-watcher.ResultChan():
			println("Received event:", resp.Events[0].Key)
		case <-ctx.Done():
			// Context canceled, watcher goroutine will exit gracefully
			println("Context canceled")
			return
		}
	}
}
