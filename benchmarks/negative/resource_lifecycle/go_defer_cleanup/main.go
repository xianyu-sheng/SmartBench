// Negative fixture: defer closes the handle.
//
// Resembles: os.Open without cleanup (resource_leak).
// Not a bug because: `defer f.Close()` runs on every return path.
// True positive if: the defer is removed, or an early return happens before it.
package main

import "os"

func readConfig(path string) ([]byte, error) {
	f, err := os.Open(path)
	if err != nil {
		return nil, err
	}
	defer f.Close()
	return os.ReadFile(path)
}
