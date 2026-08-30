package main

import "fmt"

// BatchSend writes items to a buffered channel, closes it, and returns it.
// Closing the channel signals receivers that no more values are coming,
// so a `for v := range ch` loop terminates cleanly.
func BatchSend(items []int) chan int {
	ch := make(chan int, len(items))
	for _, item := range items {
		ch <- item
	}
	close(ch) // FIX: signal completion to receivers
	return ch
}

func main() {
	ch := BatchSend([]int{1, 2, 3})
	for v := range ch {
		fmt.Println(v)
	}
}
