package main

import (
	"net/http"
	"net/http/httptest"
	"testing"
)

func TestHealthURL(t *testing.T) {
	cases := map[string]string{
		":8081":        "http://127.0.0.1:8081/healthz",
		"0.0.0.0:9000": "http://127.0.0.1:9000/healthz",
		"bad":          "http://127.0.0.1:8081/healthz",
	}
	for addr, want := range cases {
		if got := healthURL(addr); got != want {
			t.Errorf("healthURL(%q) = %q, want %q", addr, got, want)
		}
	}
}

func TestHealthcheck(t *testing.T) {
	ok := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {}))
	defer ok.Close()
	down := httptest.NewServer(http.HandlerFunc(func(w http.ResponseWriter, _ *http.Request) {
		w.WriteHeader(http.StatusServiceUnavailable)
	}))
	defer down.Close()
	if healthcheck(ok.URL) != 0 {
		t.Error("healthy server reported unhealthy")
	}
	if healthcheck(down.URL) != 1 {
		t.Error("503 reported healthy")
	}
	if healthcheck("http://127.0.0.1:1/healthz") != 1 {
		t.Error("closed port reported healthy")
	}
}
