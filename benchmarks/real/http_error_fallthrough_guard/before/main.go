package main

import (
	"encoding/json"
	"net/http"
)

type Response struct {
	Data string `json:"data"`
}

// handleRequest processes an HTTP request.
// BUG: after writing the error response with http.Error, the handler does
// not return. Execution falls through to writeJSON, which calls
// w.WriteHeader(200) on a response that already has a 500 status written.
// The second WriteHeader is silently ignored, but writeJSON's body is also
// sent, producing a corrupted response. Under some HTTP/1.1 implementations
// this splits the response or causes the client to see garbled JSON appended
// to the error text.
func handleRequest(w http.ResponseWriter, r *http.Request) {
	data, err := fetchData(r)
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		// BUG: missing return — falls through to writeJSON below
	}
	writeJSON(w, Response{Data: data})
}

func fetchData(r *http.Request) (string, error) {
	return r.URL.Query().Get("q"), nil
}

func writeJSON(w http.ResponseWriter, v any) {
	w.Header().Set("Content-Type", "application/json")
	json.NewEncoder(w).Encode(v)
}

func main() {
	http.HandleFunc("/", handleRequest)
	http.ListenAndServe(":8080", nil)
}
