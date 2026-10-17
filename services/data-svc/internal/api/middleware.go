package api

import (
	"context"
	"crypto/rand"
	"encoding/hex"
	"log/slog"
	"net/http"
	"regexp"
	"strings"
	"sync"
	"time"

	"golang.org/x/time/rate"

	"indagent/data-svc/internal/auth"
)

type ctxKey int

const (
	claimsKey ctxKey = iota
	infoKey
)

var tracePattern = regexp.MustCompile(`^[A-Za-z0-9-]{1,64}$`)

// requestInfo is filled in by inner middleware so the access log can include it.
type requestInfo struct {
	traceID string
	tenant  string
	sub     string
}

type statusRecorder struct {
	http.ResponseWriter
	status int
}

func (r *statusRecorder) WriteHeader(code int) {
	r.status = code
	r.ResponseWriter.WriteHeader(code)
}

func newTraceID() string {
	b := make([]byte, 16)
	_, _ = rand.Read(b)
	return hex.EncodeToString(b)
}

func withLogging(logger *slog.Logger, now func() time.Time, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		start := now()
		info := &requestInfo{traceID: r.Header.Get("X-Trace-Id")}
		if !tracePattern.MatchString(info.traceID) {
			info.traceID = newTraceID()
		}
		w.Header().Set("X-Trace-Id", info.traceID)
		rec := &statusRecorder{ResponseWriter: w, status: http.StatusOK}
		next.ServeHTTP(rec, r.WithContext(context.WithValue(r.Context(), infoKey, info)))
		logger.Info("request",
			"trace_id", info.traceID, "method", r.Method, "path", r.URL.Path,
			"status", rec.status, "duration_ms", now().Sub(start).Milliseconds(),
			"tenant", info.tenant, "sub", info.sub)
	})
}

func withAuth(v *auth.Verifier, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		raw, ok := strings.CutPrefix(r.Header.Get("Authorization"), "Bearer ")
		claims, err := v.Verify(strings.TrimSpace(raw))
		if !ok || err != nil {
			w.Header().Set("WWW-Authenticate", `Bearer realm="ind-agent"`)
			writeError(w, &apiError{http.StatusUnauthorized, "unauthorized", "missing or invalid token"})
			return
		}
		if info, ok := r.Context().Value(infoKey).(*requestInfo); ok {
			info.tenant, info.sub = claims.TenantID, claims.Subject
		}
		next.ServeHTTP(w, r.WithContext(context.WithValue(r.Context(), claimsKey, claims)))
	})
}

func claimsFrom(r *http.Request) *auth.Claims {
	c, _ := r.Context().Value(claimsKey).(*auth.Claims)
	return c
}

type rateLimiter struct {
	mu       sync.Mutex
	limiters map[string]*rate.Limiter
	limit    rate.Limit
	burst    int
}

func newRateLimiter(perSecond float64, burst int) *rateLimiter {
	return &rateLimiter{limiters: map[string]*rate.Limiter{}, limit: rate.Limit(perSecond), burst: burst}
}

func (l *rateLimiter) allow(key string) bool {
	l.mu.Lock()
	lim, ok := l.limiters[key]
	if !ok {
		lim = rate.NewLimiter(l.limit, l.burst)
		l.limiters[key] = lim
	}
	l.mu.Unlock()
	return lim.Allow()
}

func withRateLimit(l *rateLimiter, next http.Handler) http.Handler {
	return http.HandlerFunc(func(w http.ResponseWriter, r *http.Request) {
		c := claimsFrom(r)
		if !l.allow(c.TenantID + ":" + c.Subject) {
			w.Header().Set("Retry-After", "1")
			writeError(w, errRateLimited)
			return
		}
		next.ServeHTTP(w, r)
	})
}

func onlyGET(next http.HandlerFunc) http.HandlerFunc {
	return func(w http.ResponseWriter, r *http.Request) {
		if r.Method != http.MethodGet {
			w.Header().Set("Allow", http.MethodGet)
			writeError(w, errMethod)
			return
		}
		next(w, r)
	}
}
