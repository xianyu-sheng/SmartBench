// Negative fixture: a nil guard clause.
//
// Resembles: dereferencing a possibly-nil pointer (null_dereference).
// Not a bug because: the nil check returns before the field access.
// True positive if: the check is removed or the dereference moves above it.
package main

type Config struct {
	Name string
}

func describe(config *Config) string {
	if config == nil {
		return "unknown"
	}
	return config.Name
}
