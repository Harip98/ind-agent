package api

import (
	"context"
	"encoding/json"
	"io"
	"log/slog"
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"

	"indagent/data-svc/internal/auth"
	"indagent/data-svc/internal/store"
)

var (
	testSecret = []byte("0123456789abcdef0123456789abcdef")
	testNow    = time.Date(2026, 10, 17, 8, 0, 0, 0, time.UTC)
)

type call struct {
	tenant, asset, tag string
	from, to           time.Time
	limit, bucket      int
}

type fakeStore struct {
	calls  []call
	alarms []store.Alarm
	err    error
}

func (f *fakeStore) Assets(_ context.Context, tenant string) (store.AssetList, error) {
	f.calls = append(f.calls, call{tenant: tenant})
	return store.AssetList{Tenant: store.Tenant{ID: tenant}, Assets: []store.Asset{}}, f.err
}

func (f *fakeStore) Alarms(_ context.Context, tenant, asset string, from, to time.Time, limit int) ([]store.Alarm, error) {
	f.calls = append(f.calls, call{tenant: tenant, asset: asset, from: from, to: to, limit: limit})
	return f.alarms, f.err
}

func (f *fakeStore) Window(_ context.Context, tenant, asset, tag string, from, to time.Time, bucket int) (store.Window, error) {
	f.calls = append(f.calls, call{tenant: tenant, asset: asset, tag: tag, from: from, to: to, bucket: bucket})
	return store.Window{AssetID: asset, Tag: tag, ResolutionSeconds: bucket, Points: []store.Point{}}, f.err
}

func token(t *testing.T, tenant, sub string) string {
	t.Helper()
	s, err := jwt.NewWithClaims(jwt.SigningMethodHS256, jwt.MapClaims{
		"iss": auth.Issuer, "aud": auth.Audience, "sub": sub, "tenant_id": tenant,
		"iat": testNow.Add(-time.Minute).Unix(), "exp": testNow.Add(30 * time.Minute).Unix(),
	}).SignedString(testSecret)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func newTestRouter(t *testing.T, fs *fakeStore, burst int) http.Handler {
	t.Helper()
	v, err := auth.NewVerifier(testSecret, func() time.Time { return testNow })
	if err != nil {
		t.Fatal(err)
	}
	return NewRouter(Deps{
		Store: fs, Verifier: v, Logger: slog.New(slog.NewTextHandler(io.Discard, nil)),
		RatePerSecond: 0.001, Burst: burst, Now: func() time.Time { return testNow }, // no refill during a test
	})
}

func do(h http.Handler, method, path, bearer string) *httptest.ResponseRecorder {
	req := httptest.NewRequest(method, path, nil)
	if bearer != "" {
		req.Header.Set("Authorization", "Bearer "+bearer)
	}
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	return rec
}

func errorCode(t *testing.T, rec *httptest.ResponseRecorder) string {
	t.Helper()
	var body struct {
		Error struct{ Code, Message string } `json:"error"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatalf("non-JSON error body %q: %v", rec.Body.String(), err)
	}
	return body.Error.Code
}

func TestHealthzNeedsNoToken(t *testing.T) {
	rec := do(newTestRouter(t, &fakeStore{}, 10), http.MethodGet, "/healthz", "")
	if rec.Code != http.StatusOK || strings.TrimSpace(rec.Body.String()) != `{"status":"ok"}` {
		t.Fatalf("got %d %q", rec.Code, rec.Body.String())
	}
}

func TestErrorsAreJSON(t *testing.T) {
	h := newTestRouter(t, &fakeStore{}, 10)
	tests := []struct {
		name, method, path, bearer string
		status                     int
		code                       string
	}{
		{"no token", http.MethodGet, "/v1/assets", "", 401, "unauthorized"},
		{"bad token", http.MethodGet, "/v1/assets", "garbage", 401, "unauthorized"},
		{"unknown route", http.MethodGet, "/nope", "", 404, "not_found"},
		{"wrong method", http.MethodPost, "/v1/assets", token(t, "acme", "u"), 405, "method_not_allowed"},
		{"bad asset id", http.MethodGet, "/v1/assets/P%2012/alarms?from=2026-10-16T00:00:00Z&to=2026-10-17T00:00:00Z", token(t, "acme", "u"), 404, "not_found"},
		{"alarm window too large", http.MethodGet, "/v1/assets/P-12/alarms?from=2026-09-01T00:00:00Z&to=2026-10-17T00:00:00Z", token(t, "acme", "u"), 400, "window_too_large"},
		{"sensor window too large", http.MethodGet, "/v1/assets/P-12/sensors/TT-101/window?from=2026-10-13T00:00:00Z&to=2026-10-17T00:00:00Z", token(t, "acme", "u"), 400, "window_too_large"},
		{"missing range", http.MethodGet, "/v1/assets/P-12/alarms", token(t, "acme", "u"), 400, "invalid_range"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rec := do(h, tt.method, tt.path, tt.bearer)
			if rec.Code != tt.status {
				t.Fatalf("status = %d, want %d (%s)", rec.Code, tt.status, rec.Body.String())
			}
			if got := errorCode(t, rec); got != tt.code {
				t.Fatalf("code = %q, want %q", got, tt.code)
			}
			if ct := rec.Header().Get("Content-Type"); ct != "application/json" {
				t.Fatalf("content-type = %q", ct)
			}
		})
	}
}

func TestAssetsUseTokenTenantNotQuery(t *testing.T) {
	fs := &fakeStore{}
	rec := do(newTestRouter(t, fs, 10), http.MethodGet, "/v1/assets?tenant_id=acme", token(t, "globex", "u"))
	if rec.Code != 200 {
		t.Fatalf("status %d", rec.Code)
	}
	if len(fs.calls) != 1 || fs.calls[0].tenant != "globex" {
		t.Fatalf("calls = %+v", fs.calls)
	}
}

func TestStoreNotFoundIs404(t *testing.T) {
	fs := &fakeStore{err: store.ErrNotFound}
	rec := do(newTestRouter(t, fs, 10), http.MethodGet,
		"/v1/assets/P-12/alarms?from=2026-10-16T00:00:00Z&to=2026-10-17T00:00:00Z", token(t, "globex", "u"))
	if rec.Code != 404 || errorCode(t, rec) != "not_found" {
		t.Fatalf("got %d %s", rec.Code, rec.Body.String())
	}
}

func TestAlarmsTruncatedBeyondLimit(t *testing.T) {
	fs := &fakeStore{alarms: make([]store.Alarm, MaxAlarmRows+1)}
	rec := do(newTestRouter(t, fs, 10), http.MethodGet,
		"/v1/assets/P-12/alarms?from=2026-10-16T00:00:00Z&to=2026-10-17T00:00:00Z", token(t, "acme", "u"))
	var body struct {
		Alarms    []store.Alarm `json:"alarms"`
		Truncated bool          `json:"truncated"`
	}
	if err := json.Unmarshal(rec.Body.Bytes(), &body); err != nil {
		t.Fatal(err)
	}
	if len(body.Alarms) != MaxAlarmRows || !body.Truncated {
		t.Fatalf("len=%d truncated=%v", len(body.Alarms), body.Truncated)
	}
	if fs.calls[0].limit != MaxAlarmRows+1 {
		t.Fatalf("store limit = %d", fs.calls[0].limit)
	}
}

func TestWindowPassesBucket(t *testing.T) {
	fs := &fakeStore{}
	rec := do(newTestRouter(t, fs, 10), http.MethodGet,
		"/v1/assets/P-12/sensors/TT-101/window?from=2026-10-14T00:00:00Z&to=2026-10-17T00:00:00Z", token(t, "acme", "u"))
	if rec.Code != 200 {
		t.Fatalf("status %d %s", rec.Code, rec.Body.String())
	}
	if c := fs.calls[0]; c.tenant != "acme" || c.asset != "P-12" || c.tag != "TT-101" || c.bucket != 130 {
		t.Fatalf("call = %+v", c)
	}
}

func TestRateLimitPerToken(t *testing.T) {
	h := newTestRouter(t, &fakeStore{}, 2)
	tok := token(t, "acme", "u")
	for i := 0; i < 2; i++ {
		if rec := do(h, http.MethodGet, "/v1/assets", tok); rec.Code != 200 {
			t.Fatalf("request %d: %d", i, rec.Code)
		}
	}
	rec := do(h, http.MethodGet, "/v1/assets", tok)
	if rec.Code != 429 || errorCode(t, rec) != "rate_limited" {
		t.Fatalf("got %d %s", rec.Code, rec.Body.String())
	}
	if rec := do(h, http.MethodGet, "/v1/assets", token(t, "acme", "other-user")); rec.Code != 200 {
		t.Fatalf("other user limited: %d", rec.Code)
	}
}

func TestTraceIDEchoedOrGenerated(t *testing.T) {
	h := newTestRouter(t, &fakeStore{}, 10)
	req := httptest.NewRequest(http.MethodGet, "/healthz", nil)
	req.Header.Set("X-Trace-Id", "abc-123")
	rec := httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if got := rec.Header().Get("X-Trace-Id"); got != "abc-123" {
		t.Fatalf("trace = %q", got)
	}
	req.Header.Set("X-Trace-Id", "bad value!")
	rec = httptest.NewRecorder()
	h.ServeHTTP(rec, req)
	if got := rec.Header().Get("X-Trace-Id"); got == "bad value!" || len(got) != 32 {
		t.Fatalf("trace = %q", got)
	}
}
