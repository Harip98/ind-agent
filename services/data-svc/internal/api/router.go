// Package api holds the HTTP routes served by data-svc.
package api

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"time"

	"indagent/data-svc/internal/auth"
	"indagent/data-svc/internal/store"
)

// Store is the data access the handlers need.
type Store interface {
	Assets(ctx context.Context, tenantID string) (store.AssetList, error)
	Alarms(ctx context.Context, tenantID, assetID string, from, to time.Time, limit int) ([]store.Alarm, error)
	Window(ctx context.Context, tenantID, assetID, tag string, from, to time.Time, bucketSeconds int) (store.Window, error)
}

// Deps wires the router.
type Deps struct {
	Store         Store
	Verifier      *auth.Verifier
	Logger        *slog.Logger
	RatePerSecond float64
	Burst         int
	Now           func() time.Time
}

type handlers struct {
	store  Store
	logger *slog.Logger
}

// NewRouter returns the data-svc HTTP handler.
func NewRouter(d Deps) http.Handler {
	h := &handlers{store: d.Store, logger: d.Logger}
	limiter := newRateLimiter(d.RatePerSecond, d.Burst)
	protected := func(fn http.HandlerFunc) http.Handler {
		return withAuth(d.Verifier, withRateLimit(limiter, onlyGET(fn)))
	}
	mux := http.NewServeMux()
	mux.HandleFunc("/healthz", onlyGET(health))
	mux.Handle("/v1/assets", protected(h.assets))
	mux.Handle("/v1/assets/{asset_id}/alarms", protected(h.alarms))
	mux.Handle("/v1/assets/{asset_id}/sensors/{tag}/window", protected(h.window))
	mux.HandleFunc("/", func(w http.ResponseWriter, _ *http.Request) { writeError(w, errNotFound) })
	return withLogging(d.Logger, d.Now, mux)
}

func health(w http.ResponseWriter, _ *http.Request) {
	writeJSON(w, http.StatusOK, map[string]string{"status": "ok"})
}

func (h *handlers) fail(w http.ResponseWriter, r *http.Request, err error) {
	if errors.Is(err, store.ErrNotFound) {
		writeError(w, errNotFound)
		return
	}
	h.logger.Error("store error", "path", r.URL.Path, "err", err)
	writeError(w, errInternal)
}

func (h *handlers) assets(w http.ResponseWriter, r *http.Request) {
	list, err := h.store.Assets(r.Context(), claimsFrom(r).TenantID)
	if err != nil {
		h.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, list)
}

func (h *handlers) alarms(w http.ResponseWriter, r *http.Request) {
	assetID := r.PathValue("asset_id")
	if !validID(assetID) {
		writeError(w, errNotFound)
		return
	}
	from, to, perr := parseWindow(r.URL.Query(), MaxAlarmWindow)
	if perr != nil {
		writeError(w, perr)
		return
	}
	alarms, err := h.store.Alarms(r.Context(), claimsFrom(r).TenantID, assetID, from, to, MaxAlarmRows+1)
	if err != nil {
		h.fail(w, r, err)
		return
	}
	truncated := len(alarms) > MaxAlarmRows
	if truncated {
		alarms = alarms[:MaxAlarmRows]
	}
	writeJSON(w, http.StatusOK, map[string]any{
		"asset_id": assetID, "from": from, "to": to, "alarms": alarms, "truncated": truncated,
	})
}

func (h *handlers) window(w http.ResponseWriter, r *http.Request) {
	assetID, tag := r.PathValue("asset_id"), r.PathValue("tag")
	if !validID(assetID) || !validID(tag) {
		writeError(w, errNotFound)
		return
	}
	from, to, perr := parseWindow(r.URL.Query(), MaxSensorWindow)
	if perr != nil {
		writeError(w, perr)
		return
	}
	win, err := h.store.Window(r.Context(), claimsFrom(r).TenantID, assetID, tag, from, to, bucketSeconds(from, to))
	if err != nil {
		h.fail(w, r, err)
		return
	}
	writeJSON(w, http.StatusOK, win)
}
