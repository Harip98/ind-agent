package api

import (
	"context"
	"io"
	"log/slog"
	"net/http"
	"os"
	"testing"
	"time"

	"indagent/data-svc/internal/auth"
	"indagent/data-svc/internal/store"
)

func TestRouterCrossTenantAssetIs404(t *testing.T) {
	if os.Getenv("INDAGENT_INTEGRATION") != "1" {
		t.Skip("set INDAGENT_INTEGRATION=1")
	}
	ctx := context.Background()
	s, err := store.Open(ctx, "host=127.0.0.1 port=5433 dbname=indagent user=ops_reader password=ops_reader_dev_pw")
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	v, _ := auth.NewVerifier(testSecret, func() time.Time { return testNow })
	h := NewRouter(Deps{Store: s, Verifier: v, Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
		RatePerSecond: 100, Burst: 100, Now: time.Now})
	// acme's P-12 exists (simgen); globex must not be able to tell.
	path := "/v1/assets/P-12/alarms?from=2026-01-01T00:00:00Z&to=2026-01-02T00:00:00Z"
	rec := do(h, http.MethodGet, path, token(t, "globex", "u"))
	if rec.Code != http.StatusNotFound || errorCode(t, rec) != "not_found" {
		t.Fatalf("got %d %s", rec.Code, rec.Body.String())
	}
	rec = do(h, http.MethodGet, path, token(t, "acme", "u"))
	if rec.Code != http.StatusOK {
		t.Fatalf("acme got %d %s", rec.Code, rec.Body.String())
	}
}
