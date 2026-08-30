package main

import (
	"fmt"
	"sync"
)

type Cache struct {
	mu   sync.RWMutex
	data map[string]string
}

// Get retrieves a value but may leak the read lock on error paths
func (c *Cache) Get(key string) (string, error) {
	c.mu.RLock()
	// BUG: No defer c.mu.RUnlock()

	if key == "" {
		// Error path returns without unlocking
		return "", fmt.Errorf("empty key")
	}

	value, ok := c.data[key]
	c.mu.RUnlock() // Only unlocks on success path

	if !ok {
		return "", fmt.Errorf("key not found")
	}
	return value, nil
}

// Set updates a value but may leak the write lock
func (c *Cache) Set(key, value string) error {
	c.mu.Lock()
	// BUG: No defer c.mu.Unlock()

	if key == "" {
		// Error path returns without unlocking
		return fmt.Errorf("empty key")
	}

	if c.data == nil {
		c.data = make(map[string]string)
	}

	c.data[key] = value
	c.mu.Unlock() // Only unlocks on success path
	return nil
}

func main() {
	cache := &Cache{data: make(map[string]string)}

	// First call succeeds
	_ = cache.Set("key1", "value1")

	// Second call with empty key triggers the bug
	// The write lock is never released
	_ = cache.Set("", "value2")

	// This call will deadlock because write lock is still held
	fmt.Println("Attempting to read...")
	_, _ = cache.Get("key1")
	fmt.Println("This line will never execute")
}
