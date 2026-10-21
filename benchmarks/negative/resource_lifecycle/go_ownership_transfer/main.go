// Negative fixture: ownership is transferred to the caller.
//
// Resembles: a handle opened without a local close (resource_leak).
// Not a bug because: returning the handle makes the caller responsible for it.
// True positive if: the caller never closes the returned handle (not visible here).
package main

import "os"

// openConfig returns an open handle; the caller owns it.
func openConfig(path string) (*os.File, error) {
	return os.Open(path)
}
