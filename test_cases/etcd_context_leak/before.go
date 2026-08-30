// Case 4: etcd/Kubernetes context cancellation goroutine leak
// Source: https://github.com/kubernetes/kubernetes/pull/25331
// Pattern: Context canceled but goroutine not properly terminated
// Impact: Goroutine leak in etcd client when context is canceled

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
// BUG: When ctx is canceled, the internal goroutine may not exit properly
func NewWatcher(ctx context.Context, key string) *Watcher {
	w := &Watcher{
		resultChan: make(chan WatchResponse, 10),
		ctx:        ctx,
	}

	// Start background goroutine to watch for events
	go func() {
		// BUG: This goroutine blocks on resultChan send
		// If the consumer cancels context and stops reading,
		// this goroutine will leak
		for {
			// Simulate receiving watch events
			resp := WatchResponse{
				Events: []Event{{Key: key, Value: "value"}},
			}

			// PROBLEM: This send blocks forever if no one is reading
			// and context cancellation doesn't stop it
			w.resultChan <- resp

			time.Sleep(100 * time.Millisecond)
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
			// Context canceled, but the watcher goroutine is still running
			// LEAK: goroutine blocks on resultChan <- resp forever
			println("Context canceled")
			return
		}
	}
}
