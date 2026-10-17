package store

import (
	"context"
	"errors"
	"os"
	"testing"
	"time"

	"github.com/jackc/pgx/v5"
)

const (
	readerDSN = "host=localhost port=5433 dbname=indagent user=ops_reader password=ops_reader_dev_pw"
	loaderDSN = "host=localhost port=5433 dbname=indagent user=ops_loader password=ops_loader_dev_pw"
	ownerDSN  = "host=localhost port=5433 dbname=indagent user=app_owner password=owner_dev_pw"
)

var seedStart = time.Date(2026, 1, 1, 0, 0, 0, 0, time.UTC)

func integration(t *testing.T) {
	t.Helper()
	if os.Getenv("INDAGENT_INTEGRATION") != "1" {
		t.Skip("set INDAGENT_INTEGRATION=1 to run against the compose Postgres")
	}
}

// seed creates gotest_a (asset P-1, 72h of 1-minute TT-1 readings, one alarm) and gotest_b (asset P-2).
func seed(t *testing.T) {
	t.Helper()
	ctx := context.Background()
	conn, err := pgx.Connect(ctx, loaderDSN)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Close(ctx)
	for tenant, asset := range map[string]string{"gotest_a": "P-1", "gotest_b": "P-2"} {
		tx, err := conn.Begin(ctx)
		if err != nil {
			t.Fatal(err)
		}
		stmts := []string{
			"SELECT set_config('app.tenant_id', $1, true)",
			"DELETE FROM ops.sensor_readings WHERE tenant_id = $1",
			"DELETE FROM ops.alarms WHERE tenant_id = $1",
			"DELETE FROM ops.sensors WHERE tenant_id = $1",
			"DELETE FROM ops.assets WHERE tenant_id = $1",
			"DELETE FROM ops.tenants WHERE id = $1",
			"INSERT INTO ops.tenants (id, name, timezone) VALUES ($1, $1, 'UTC')",
		}
		for _, s := range stmts {
			if _, err := tx.Exec(ctx, s, tenant); err != nil {
				t.Fatal(s, err)
			}
		}
		mustExec := func(sql string, args ...any) {
			if _, err := tx.Exec(ctx, sql, args...); err != nil {
				t.Fatal(sql, err)
			}
		}
		mustExec("INSERT INTO ops.assets (tenant_id, id, name, asset_model, site) VALUES ($1, $2, $2, 'CP-200', 'Test')", tenant, asset)
		mustExec("INSERT INTO ops.sensors (tenant_id, asset_id, tag, kind, description, unit, hi_limit, related_tags)"+
			" VALUES ($1, $2, 'TT-1', 'temperature', 'Bearing temp', 'degC', 85, '{}')", tenant, asset)
		mustExec("INSERT INTO ops.sensor_readings (tenant_id, asset_id, tag, ts, value)"+
			" SELECT $1, $2, 'TT-1', ts, 60 FROM generate_series($3::timestamptz, $3::timestamptz + interval '72 hours' - interval '1 minute', interval '1 minute') ts",
			tenant, asset, seedStart)
		mustExec("INSERT INTO ops.alarms (tenant_id, asset_id, tag, alarm_type, severity, raised_at, message)"+
			" VALUES ($1, $2, 'TT-1', 'high_temp', 'medium', $3, 'test')", tenant, asset, seedStart.Add(time.Hour))
		if err := tx.Commit(ctx); err != nil {
			t.Fatal(err)
		}
	}
}

func openReader(t *testing.T) *Store {
	t.Helper()
	s, err := Open(context.Background(), readerDSN)
	if err != nil {
		t.Fatal(err)
	}
	t.Cleanup(s.Close)
	return s
}

func TestStoreAssetsScopedToTenant(t *testing.T) {
	integration(t)
	seed(t)
	list, err := openReader(t).Assets(context.Background(), "gotest_b")
	if err != nil {
		t.Fatal(err)
	}
	if list.Tenant.ID != "gotest_b" || len(list.Assets) != 1 || list.Assets[0].ID != "P-2" {
		t.Fatalf("list = %+v", list)
	}
	if len(list.Assets[0].Sensors) != 1 || *list.Assets[0].Sensors[0].HiLimit != 85 {
		t.Fatalf("sensors = %+v", list.Assets[0].Sensors)
	}
}

func TestStoreOtherTenantAssetNotFound(t *testing.T) {
	integration(t)
	seed(t)
	s := openReader(t)
	ctx := context.Background()
	if _, err := s.Alarms(ctx, "gotest_b", "P-1", seedStart, seedStart.Add(24*time.Hour), 10); !errors.Is(err, ErrNotFound) {
		t.Fatalf("alarms err = %v", err)
	}
	if _, err := s.Window(ctx, "gotest_b", "P-1", "TT-1", seedStart, seedStart.Add(time.Hour), 60); !errors.Is(err, ErrNotFound) {
		t.Fatalf("window err = %v", err)
	}
	if _, err := s.Window(ctx, "gotest_a", "P-1", "NOPE", seedStart, seedStart.Add(time.Hour), 60); !errors.Is(err, ErrNotFound) {
		t.Fatalf("unknown tag err = %v", err)
	}
}

func TestStoreAlarmsHaveOpaqueIDs(t *testing.T) {
	integration(t)
	seed(t)
	alarms, err := openReader(t).Alarms(context.Background(), "gotest_a", "P-1", seedStart, seedStart.Add(24*time.Hour), 10)
	if err != nil {
		t.Fatal(err)
	}
	if len(alarms) != 1 || alarms[0].ID != "P-1:TT-1:high_temp:20260101T010000Z" {
		t.Fatalf("alarms = %+v", alarms)
	}
}

func TestStoreWindowDownsamples(t *testing.T) {
	integration(t)
	seed(t)
	w, err := openReader(t).Window(context.Background(), "gotest_a", "P-1", "TT-1", seedStart, seedStart.Add(72*time.Hour), 130)
	if err != nil {
		t.Fatal(err)
	}
	if n := len(w.Points); n > 2000 || n < 1990 {
		t.Fatalf("points = %d", n)
	}
	if w.Unit != "degC" || w.Points[0].N < 2 || *w.Points[0].Avg != 60 {
		t.Fatalf("first point = %+v unit %q", w.Points[0], w.Unit)
	}
}

func TestStoreRefusesPrivilegedRole(t *testing.T) {
	integration(t)
	ctx := context.Background()
	owner, err := Open(ctx, ownerDSN)
	if err != nil {
		t.Fatal(err)
	}
	defer owner.Close()
	if err := owner.CheckNotPrivileged(ctx); err == nil {
		t.Fatal("owner role accepted")
	}
	if err := openReader(t).CheckNotPrivileged(ctx); err != nil {
		t.Fatalf("reader rejected: %v", err)
	}
}

func TestPoolResetsTenantAfterRelease(t *testing.T) {
	integration(t)
	seed(t)
	ctx := context.Background()
	// One connection, so the second Acquire must wait for the released (and reset) one.
	s, err := Open(ctx, readerDSN+" pool_max_conns=1")
	if err != nil {
		t.Fatal(err)
	}
	defer s.Close()
	conn, err := s.Pool().Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	if _, err := conn.Exec(ctx, "SELECT set_config('app.tenant_id', 'gotest_a', false)"); err != nil {
		t.Fatal(err)
	}
	pid := conn.Conn().PgConn().PID()
	conn.Release()
	conn, err = s.Pool().Acquire(ctx)
	if err != nil {
		t.Fatal(err)
	}
	defer conn.Release()
	if conn.Conn().PgConn().PID() != pid {
		t.Fatal("expected the same pooled connection")
	}
	var tenant string
	if err := conn.QueryRow(ctx, "SELECT coalesce(current_setting('app.tenant_id', true), '')").Scan(&tenant); err != nil {
		t.Fatal(err)
	}
	if tenant != "" {
		t.Fatalf("tenant leaked across pool release: %q", tenant)
	}
}
