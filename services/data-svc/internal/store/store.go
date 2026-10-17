// Package store is the read-only, tenant-scoped access layer over the ops schema.
package store

import (
	"context"
	"errors"
	"fmt"
	"time"

	"github.com/jackc/pgx/v5"
	"github.com/jackc/pgx/v5/pgxpool"
)

// ErrNotFound means the asset or tag does not exist for the caller's tenant.
var ErrNotFound = errors.New("not found")

type Tenant struct {
	ID       string `json:"id"`
	Name     string `json:"name"`
	Timezone string `json:"timezone"`
}

type Sensor struct {
	Tag         string   `json:"tag"`
	Kind        string   `json:"kind"`
	Description string   `json:"description"`
	Unit        string   `json:"unit"`
	LoLimit     *float64 `json:"lo_limit"`
	HiLimit     *float64 `json:"hi_limit"`
	RelatedTags []string `json:"related_tags"`
}

type Asset struct {
	ID      string   `json:"id"`
	Name    string   `json:"name"`
	Model   string   `json:"model"`
	Site    string   `json:"site"`
	Sensors []Sensor `json:"sensors"`
}

type AssetList struct {
	Tenant Tenant  `json:"tenant"`
	Assets []Asset `json:"assets"`
}

type Alarm struct {
	ID         string     `json:"id"`
	AssetID    string     `json:"asset_id"`
	Tag        string     `json:"tag"`
	AlarmType  string     `json:"alarm_type"`
	Severity   string     `json:"severity"`
	RaisedAt   time.Time  `json:"raised_at"`
	ClearedAt  *time.Time `json:"cleared_at"`
	Value      *float64   `json:"value"`
	LimitValue *float64   `json:"limit_value"`
	Message    string     `json:"message"`
}

type Point struct {
	TS  time.Time `json:"ts"`
	Avg *float64  `json:"avg"`
	Min *float64  `json:"min"`
	Max *float64  `json:"max"`
	N   int       `json:"n"`
	Bad int       `json:"bad"`
}

type Window struct {
	AssetID           string    `json:"asset_id"`
	Tag               string    `json:"tag"`
	Unit              string    `json:"unit"`
	From              time.Time `json:"from"`
	To                time.Time `json:"to"`
	ResolutionSeconds int       `json:"resolution_seconds"`
	Points            []Point   `json:"points"`
}

// AlarmID is the opaque, tenant-local alarm identifier exposed by the API.
func AlarmID(assetID, tag string, raisedAt time.Time) string {
	return fmt.Sprintf("%s:%s:%s", assetID, tag, raisedAt.UTC().Format("20060102T1504Z"))
}

// Store holds the connection pool.
type Store struct{ pool *pgxpool.Pool }

// Open connects and resets the tenant setting whenever a connection returns to the pool.
func Open(ctx context.Context, dsn string) (*Store, error) {
	cfg, err := pgxpool.ParseConfig(dsn)
	if err != nil {
		return nil, err
	}
	cfg.AfterRelease = func(c *pgx.Conn) bool {
		_, err := c.Exec(context.Background(), "SELECT set_config('app.tenant_id', '', false)")
		return err == nil
	}
	pool, err := pgxpool.NewWithConfig(ctx, cfg)
	if err != nil {
		return nil, err
	}
	if err := pool.Ping(ctx); err != nil {
		pool.Close()
		return nil, err
	}
	return &Store{pool: pool}, nil
}

func (s *Store) Close()               { s.pool.Close() }
func (s *Store) Pool() *pgxpool.Pool { return s.pool }

// CheckNotPrivileged fails when the connected role could bypass row-level security.
func (s *Store) CheckNotPrivileged(ctx context.Context) error {
	var role string
	var privileged bool
	err := s.pool.QueryRow(ctx,
		"SELECT rolname, rolsuper OR rolbypassrls FROM pg_roles WHERE rolname = current_user",
	).Scan(&role, &privileged)
	if err != nil {
		return err
	}
	if privileged {
		return fmt.Errorf("database role %q is superuser or BYPASSRLS; refusing to serve", role)
	}
	return nil
}

func (s *Store) withTenant(ctx context.Context, tenantID string, fn func(pgx.Tx) error) error {
	if tenantID == "" {
		return errors.New("tenant id is required")
	}
	tx, err := s.pool.BeginTx(ctx, pgx.TxOptions{AccessMode: pgx.ReadOnly})
	if err != nil {
		return err
	}
	defer tx.Rollback(ctx) //nolint:errcheck // no-op after commit
	if _, err := tx.Exec(ctx, "SELECT set_config('app.tenant_id', $1, true)", tenantID); err != nil {
		return err
	}
	if err := fn(tx); err != nil {
		return err
	}
	return tx.Commit(ctx)
}

// Assets lists the tenant and its assets with their sensor maps.
func (s *Store) Assets(ctx context.Context, tenantID string) (AssetList, error) {
	out := AssetList{Assets: []Asset{}}
	err := s.withTenant(ctx, tenantID, func(tx pgx.Tx) error {
		err := tx.QueryRow(ctx,
			"SELECT id, name, timezone FROM ops.tenants WHERE id = $1", tenantID,
		).Scan(&out.Tenant.ID, &out.Tenant.Name, &out.Tenant.Timezone)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		} else if err != nil {
			return err
		}
		rows, err := tx.Query(ctx, `
			SELECT a.id, a.name, a.asset_model, a.site,
			       s.tag, s.kind, s.description, s.unit, s.lo_limit, s.hi_limit, s.related_tags
			FROM ops.assets a JOIN ops.sensors s ON s.tenant_id = a.tenant_id AND s.asset_id = a.id
			WHERE a.tenant_id = $1
			ORDER BY a.id, s.tag`, tenantID)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var a Asset
			var sn Sensor
			if err := rows.Scan(&a.ID, &a.Name, &a.Model, &a.Site, &sn.Tag, &sn.Kind,
				&sn.Description, &sn.Unit, &sn.LoLimit, &sn.HiLimit, &sn.RelatedTags); err != nil {
				return err
			}
			if n := len(out.Assets); n == 0 || out.Assets[n-1].ID != a.ID {
				out.Assets = append(out.Assets, a)
			}
			last := &out.Assets[len(out.Assets)-1]
			last.Sensors = append(last.Sensors, sn)
		}
		return rows.Err()
	})
	return out, err
}

func assetExists(ctx context.Context, tx pgx.Tx, tenantID, assetID string) error {
	var one int
	err := tx.QueryRow(ctx,
		"SELECT 1 FROM ops.assets WHERE tenant_id = $1 AND id = $2", tenantID, assetID,
	).Scan(&one)
	if errors.Is(err, pgx.ErrNoRows) {
		return ErrNotFound
	}
	return err
}

// Alarms returns up to limit alarms raised in [from, to), oldest first.
func (s *Store) Alarms(ctx context.Context, tenantID, assetID string, from, to time.Time, limit int) ([]Alarm, error) {
	out := []Alarm{}
	err := s.withTenant(ctx, tenantID, func(tx pgx.Tx) error {
		if err := assetExists(ctx, tx, tenantID, assetID); err != nil {
			return err
		}
		rows, err := tx.Query(ctx, `
			SELECT asset_id, tag, alarm_type, severity, raised_at, cleared_at, value, limit_value, message
			FROM ops.alarms
			WHERE tenant_id = $1 AND asset_id = $2 AND raised_at >= $3 AND raised_at < $4
			ORDER BY raised_at, tag
			LIMIT $5`, tenantID, assetID, from, to, limit)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var a Alarm
			if err := rows.Scan(&a.AssetID, &a.Tag, &a.AlarmType, &a.Severity, &a.RaisedAt,
				&a.ClearedAt, &a.Value, &a.LimitValue, &a.Message); err != nil {
				return err
			}
			a.ID = AlarmID(a.AssetID, a.Tag, a.RaisedAt)
			out = append(out, a)
		}
		return rows.Err()
	})
	return out, err
}

// Window returns readings in [from, to) aggregated into bucketSeconds buckets.
// Only good-quality samples (192) contribute to avg/min/max; Bad counts the others.
func (s *Store) Window(ctx context.Context, tenantID, assetID, tag string, from, to time.Time, bucketSeconds int) (Window, error) {
	w := Window{AssetID: assetID, Tag: tag, From: from, To: to, ResolutionSeconds: bucketSeconds, Points: []Point{}}
	err := s.withTenant(ctx, tenantID, func(tx pgx.Tx) error {
		if err := assetExists(ctx, tx, tenantID, assetID); err != nil {
			return err
		}
		err := tx.QueryRow(ctx,
			"SELECT unit FROM ops.sensors WHERE tenant_id = $1 AND asset_id = $2 AND tag = $3",
			tenantID, assetID, tag,
		).Scan(&w.Unit)
		if errors.Is(err, pgx.ErrNoRows) {
			return ErrNotFound
		} else if err != nil {
			return err
		}
		rows, err := tx.Query(ctx, `
			SELECT date_bin(make_interval(secs => $5::double precision), ts, $3::timestamptz) AS bucket,
			       avg(value) FILTER (WHERE quality = 192),
			       min(value) FILTER (WHERE quality = 192),
			       max(value) FILTER (WHERE quality = 192),
			       count(*),
			       count(*) FILTER (WHERE quality <> 192)
			FROM ops.sensor_readings
			WHERE tenant_id = $1 AND asset_id = $2 AND tag = $6 AND ts >= $3 AND ts < $4
			GROUP BY bucket
			ORDER BY bucket`, tenantID, assetID, from, to, float64(bucketSeconds), tag)
		if err != nil {
			return err
		}
		defer rows.Close()
		for rows.Next() {
			var p Point
			if err := rows.Scan(&p.TS, &p.Avg, &p.Min, &p.Max, &p.N, &p.Bad); err != nil {
				return err
			}
			w.Points = append(w.Points, p)
		}
		return rows.Err()
	})
	return w, err
}
