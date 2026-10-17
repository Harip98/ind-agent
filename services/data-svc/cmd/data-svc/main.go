// Command data-svc serves read-only, tenant-scoped access to alarms and telemetry.
package main

import (
	"context"
	"errors"
	"log/slog"
	"net/http"
	"os"
	"os/signal"
	"syscall"
	"time"

	"indagent/data-svc/internal/api"
	"indagent/data-svc/internal/auth"
	"indagent/data-svc/internal/store"
)

const devDSN = "host=127.0.0.1 port=5433 dbname=indagent user=ops_reader password=ops_reader_dev_pw"

func getenv(key, def string) string {
	if v := os.Getenv(key); v != "" {
		return v
	}
	return def
}

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	if err := run(logger); err != nil {
		logger.Error("data-svc stopped", "err", err)
		os.Exit(1)
	}
}

func run(logger *slog.Logger) error {
	ctx, stop := signal.NotifyContext(context.Background(), os.Interrupt, syscall.SIGTERM)
	defer stop()

	verifier, err := auth.NewVerifier([]byte(os.Getenv("COPILOT_JWT_SECRET")), time.Now)
	if err != nil {
		return err
	}
	st, err := store.Open(ctx, getenv("DATASVC_DB_DSN", devDSN))
	if err != nil {
		return err
	}
	defer st.Close()
	if err := st.CheckNotPrivileged(ctx); err != nil {
		return err
	}

	addr := getenv("DATASVC_ADDR", ":8081")
	srv := &http.Server{
		Addr: addr,
		Handler: api.NewRouter(api.Deps{
			Store: st, Verifier: verifier, Logger: logger,
			RatePerSecond: 10, Burst: 20, Now: time.Now,
		}),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      30 * time.Second,
	}
	errc := make(chan error, 1)
	go func() { errc <- srv.ListenAndServe() }()
	logger.Info("data-svc listening", "addr", addr)
	select {
	case err := <-errc:
		return err
	case <-ctx.Done():
		shutdown, cancel := context.WithTimeout(context.Background(), 10*time.Second)
		defer cancel()
		if err := srv.Shutdown(shutdown); err != nil && !errors.Is(err, http.ErrServerClosed) {
			return err
		}
		return nil
	}
}
