package main

import (
	"fmt"
	"io"
	"net/http"
)

// FetchURL properly handles the response and closes the body.
func FetchURL(url string) (string, error) {
	// FIX: Properly handle the response and close the body
	resp, err := http.Get(url)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close() // Ensure body is closed

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

// FetchURLCompact uses a helper to ensure proper cleanup
func FetchURLCompact(url string) (string, error) {
	resp, err := http.Get(url)
	if err != nil {
		return "", err
	}
	defer resp.Body.Close()

	body, err := io.ReadAll(resp.Body)
	if err != nil {
		return "", err
	}
	return string(body), nil
}

func main() {
	// Each call properly closes the connection
	for i := 0; i < 5; i++ {
		content, err := FetchURL("https://example.com")
		if err != nil {
			fmt.Printf("Error: %v\n", err)
			continue
		}
		fmt.Printf("Fetched %d bytes\n", len(content))
	}

	// Connections are properly released back to the pool
}
