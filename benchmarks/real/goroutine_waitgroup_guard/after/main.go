package main

import (
	"fmt"
	"sync"
	"time"
)

// ProcessItemsConcurrently spawns goroutines and waits for them to complete.
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

	// FIX: Wait for all goroutines to complete before returning
	wg.Wait()
}

func main() {
	items := []string{"task1", "task2", "task3"}
	ProcessItemsConcurrently(items)
	fmt.Println("Main function finished")
	// All goroutines have completed
}
