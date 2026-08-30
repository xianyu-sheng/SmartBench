package main

import (
	"fmt"
	"time"
)

// GenerateNumbers produces numbers on a channel and closes it when done.
func GenerateNumbers(max int) <-chan int {
	ch := make(chan int)

	go func() {
		defer close(ch) // FIX: Close channel when goroutine exits
		for i := 0; i < max; i++ {
			ch <- i
			time.Sleep(10 * time.Millisecond)
		}
	}()

	return ch
}

func main() {
	numbers := GenerateNumbers(5)

	// This will print 0,1,2,3,4 then exit cleanly
	// because the channel is properly closed
	for num := range numbers {
		fmt.Println("Received:", num)
	}

	fmt.Println("All numbers received, program exits cleanly")
}
