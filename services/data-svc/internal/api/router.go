// Package api holds the HTTP routes served by data-svc.
package api

import (
	"encoding/json"
	"net/http"
)

// NewRouter returns the data-svc HTTP handler.
func NewRouter() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", health)
	return mux
}

func health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}
