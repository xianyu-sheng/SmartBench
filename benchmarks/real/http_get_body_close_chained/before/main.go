package main

import (
	"fmt"
	"io"
	"net/http"
)

// FetchURL makes an HTTP request but never closes the response body.
// The connection is held open until GC runs, exhausting the pool under load.
func FetchURL(url string) (string, error) {
	resp, err := http.Get(url)
	if err != nil {
		return "", err
	}
	// BUG: resp.Body is never closed.
	// On the error path below, and even on the success path, the body
	// stays open. The receiver of the returned string has no way to close it.

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

// FetchStatus fetches only the status code but still leaks the body.
func FetchStatus(url string) (int, error) {
	resp, err := http.Get(url)
	if err != nil {
		return 0, err
	}
	// BUG: returning early without closing resp.Body leaks the connection
	return resp.StatusCode, nil
}

func main() {
	for i := 0; i < 5; i++ {
		content, err := FetchURL("https://example.com")
		if err != nil {
			fmt.Printf("Error: %v\n", err)
			continue
		}
		fmt.Printf("Fetched %d bytes\n", len(content))
	}
	// After enough calls the connection pool is exhausted
	// and new requests start failing or hanging.
}
