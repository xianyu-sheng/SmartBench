package main

import (
	"fmt"
	"sync"
	"time"
)

// ProcessItemsConcurrently spawns goroutines and waits for them to complete.
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

	// FIX: Wait for all goroutines to complete before returning.
	wg.Wait()
	return nil
}

func main() {
	items := []string{"task1", "task2", "task3"}
	if err := ProcessItemsConcurrently(items); err != nil {
		fmt.Println("Error:", err)
	}
	fmt.Println("Main finished — all goroutines completed")
}
