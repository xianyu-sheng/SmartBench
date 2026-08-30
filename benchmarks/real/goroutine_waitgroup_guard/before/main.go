package main

import (
	"fmt"
	"sync"
	"time"
)

// ProcessItemsConcurrently spawns goroutines but never waits for them to complete.
// This leaks goroutines and may cause data races or incomplete processing.
func ProcessItemsConcurrently(items []string) error {
	var wg sync.WaitGroup

	for _, item := range items {
		wg.Add(1)
		go func(s string) {
			defer wg.Done()
			time.Sleep(100 * time.Millisecond)
			fmt.Println("Processed:", s)
		}(item)
	}

	// BUG: Returns without calling wg.Wait().
	// Goroutines may still be running when the caller proceeds.
	return nil
}

func main() {
	items := []string{"task1", "task2", "task3"}
	if err := ProcessItemsConcurrently(items); err != nil {
		fmt.Println("Error:", err)
	}
	fmt.Println("Main finished — goroutines may still be running")
}
