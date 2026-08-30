package main

import (
	"encoding/json"
	"net/http"
)

type Response struct {
	Data string `json:"data"`
}

// handleRequest processes an HTTP request.
// FIX: every error path returns immediately after writing the error response.
// writeJSON is only reachable when fetchData succeeds, so there is no
// double-write risk.
func handleRequest(w http.ResponseWriter, r *http.Request) {
	data, err := fetchData(r)
	if err != nil {
		http.Error(w, "internal error", http.StatusInternalServerError)
		return // FIX: exit the handler after the error response
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
