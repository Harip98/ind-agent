package api

import (
	"encoding/json"
	"net/http"
)

type apiError struct {
	status  int
	code    string
	message string
}

func writeJSON(w http.ResponseWriter, status int, v any) {
	w.Header().Set("Content-Type", "application/json")
	w.WriteHeader(status)
	_ = json.NewEncoder(w).Encode(v)
}

func writeError(w http.ResponseWriter, e *apiError) {
	writeJSON(w, e.status, map[string]map[string]string{
		"error": {"code": e.code, "message": e.message},
	})
}

var (
	errNotFound    = &apiError{http.StatusNotFound, "not_found", "resource not found"}
	errInternal    = &apiError{http.StatusInternalServerError, "internal", "internal error"}
	errRateLimited = &apiError{http.StatusTooManyRequests, "rate_limited", "too many requests"}
	errMethod      = &apiError{http.StatusMethodNotAllowed, "method_not_allowed", "method not allowed"}
)
