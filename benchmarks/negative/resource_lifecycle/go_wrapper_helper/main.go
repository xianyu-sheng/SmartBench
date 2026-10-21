// Negative fixture: a wrapper helper owns the cleanup.
//
// Resembles: opening a file at a call site that never closes it (resource_leak).
// Not a bug because: the helper defers Close before returning the content.
// True positive if: the helper returns the handle instead of the content.
package main

import "os"

func readFile(path string) (string, error) {
	f, err := os.Open(path)
	if err != nil {
		return "", err
	}
	defer f.Close()
	data, err := os.ReadFile(path)
	if err != nil {
		return "", err
	}
	return string(data), nil
}

func loadConfig(path string) (string, error) {
	return readFile(path)
}
