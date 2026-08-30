package main

import (
	"fmt"
	"sync"
)

type Cache struct {
	mu   sync.RWMutex
	data map[string]string
}

// Get retrieves a value with proper lock management
func (c *Cache) Get(key string) (string, error) {
	c.mu.RLock()
	defer c.mu.RUnlock() // FIX: Unlock on all exit paths

	if key == "" {
		return "", fmt.Errorf("empty key")
	}

	value, ok := c.data[key]
	if !ok {
		return "", fmt.Errorf("key not found")
	}
	return value, nil
}

// Set updates a value with proper lock management
func (c *Cache) Set(key, value string) error {
	c.mu.Lock()
	defer c.mu.Unlock() // FIX: Unlock on all exit paths

	if key == "" {
		return fmt.Errorf("empty key")
	}

	if c.data == nil {
		c.data = make(map[string]string)
	}

	c.data[key] = value
	return nil
}

func main() {
	cache := &Cache{data: make(map[string]string)}

	// First call succeeds
	_ = cache.Set("key1", "value1")

	// Second call with empty key returns error
	// but lock is properly released
	_ = cache.Set("", "value2")

	// This call succeeds because lock was released
	fmt.Println("Attempting to read...")
	val, _ := cache.Get("key1")
	fmt.Println("Successfully read:", val)
}
