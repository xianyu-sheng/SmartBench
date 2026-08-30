package main

import (
	"fmt"
	"time"
)

// GenerateNumbers produces numbers on a channel but never closes it.
// This causes consumers to block forever waiting for more data.
func GenerateNumbers(max int) <-chan int {
	ch := make(chan int)

	go func() {
		for i := 0; i < max; i++ {
			ch <- i
			time.Sleep(10 * time.Millisecond)
		}
		// BUG: Channel is never closed
		// Consumers using range will block forever
	}()

	return ch
}

func main() {
	numbers := GenerateNumbers(5)

	// This will print 0,1,2,3,4 then block forever
	// because the channel is never closed
	for num := range numbers {
		fmt.Println("Received:", num)
	}

	fmt.Println("This line will never execute")
}
