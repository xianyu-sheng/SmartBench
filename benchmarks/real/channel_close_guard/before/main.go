package main

import "fmt"

// BatchSend writes items to a buffered channel and returns it.
// Receivers use range to read, but range only terminates when the channel
// is closed — so they block forever.
func BatchSend(items []int) chan int {
	ch := make(chan int, len(items))
	for _, item := range items {
		ch <- item
	}
	// BUG: ch is never closed.
	// Any receiver doing `for v := range ch` will block waiting for more
	// values that will never arrive.
	return ch
}

func main() {
	ch := BatchSend([]int{1, 2, 3})
	// This loop blocks forever because BatchSend never closes ch.
	for v := range ch {
		fmt.Println(v)
	}
}
