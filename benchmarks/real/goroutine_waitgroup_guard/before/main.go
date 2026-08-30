package main

import (
	"fmt"
	"sync"
	"time"
)

// ProcessItemsConcurrently spawns goroutines but never waits for them to complete.
// This leaks goroutines and may cause data races or incomplete processing.
func ProcessItemsConcurrently(items []string) {
	var wg sync.WaitGroup

	for _, item := range items {
		wg.Add(1)
		go func(s string) {
			defer wg.Done()
			// Simulate processing
			time.Sleep(100 * time.Millisecond)
			fmt.Println("Processed:", s)
		}(item)
	}

	// BUG: Function returns without calling wg.Wait()
	// Goroutines may still be running when function returns
	// This can cause incomplete processing or data corruption
}

func main() {
	items := []string{"task1", "task2", "task3"}
	ProcessItemsConcurrently(items)
	fmt.Println("Main function finished")
	// The goroutines are still running but we've already returned
}
