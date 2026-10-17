"""Write a SimResult to Postgres, one tenant per transaction, through row-level security."""

import json
from datetime import timedelta
from pathlib import Path

import numpy as np
import psycopg
from psycopg import sql

from copilot.config import Settings
from copilot.simgen.catalog import MODELS
from copilot.simgen.generate import SimResult

COUNT_TABLES = ("assets", "sensors", "alarms", "sensor_readings")


def _as_tenant(conn: psycopg.Connection, tenant_id: str) -> None:
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


def _delete_tenant(conn: psycopg.Connection, tenant_id: str) -> None:
    for table in ("sensor_readings", "alarms", "sensors", "assets"):
        conn.execute(
            sql.SQL("DELETE FROM ops.{} WHERE tenant_id = %s").format(sql.Identifier(table)),
            (tenant_id,),
        )
    conn.execute("DELETE FROM ops.tenants WHERE id = %s", (tenant_id,))


def load(result: SimResult, settings: Settings) -> None:
    n = int((result.end - result.start).total_seconds() // 60)
    timestamps = [result.start + timedelta(minutes=i) for i in range(n)]
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        for tenant in result.tenants:
            assets = [a for a in result.assets if a.tenant_id == tenant.id]
            with conn.transaction():
                _as_tenant(conn, tenant.id)
                _delete_tenant(conn, tenant.id)
                conn.execute(
                    "INSERT INTO ops.tenants (id, name, timezone) VALUES (%s, %s, %s)",
                    (tenant.id, tenant.name, tenant.timezone),
                )
                with conn.cursor() as cur:
                    cur.executemany(
                        "INSERT INTO ops.assets (tenant_id, id, name, asset_model, site)"
                        " VALUES (%s, %s, %s, %s, %s)",
                        [(a.tenant_id, a.id, a.name, a.model, a.site) for a in assets],
                    )
                    cur.executemany(
                        "INSERT INTO ops.sensors (tenant_id, asset_id, tag, kind, description,"
                        " unit, lo_limit, hi_limit, related_tags)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [
                            (a.tenant_id, a.id, s.tag, s.kind, s.description, s.unit,
                             s.lo_limit, s.hi_limit, list(s.related))
                            for a in assets
                            for s in MODELS[a.model].sensors
                        ],
                    )
                    # COPY FROM is not allowed on RLS tables: stage in a temp table, then
                    # INSERT ... SELECT so the RLS WITH CHECK still applies to every row.
                    cur.execute(
                        "CREATE TEMP TABLE readings_stage (tenant_id text, asset_id text,"
                        " tag text, ts timestamptz, value double precision, quality smallint)"
                        " ON COMMIT DROP"
                    )
                    with cur.copy(
                        "COPY readings_stage (tenant_id, asset_id, tag, ts, value, quality)"
                        " FROM STDIN"
                    ) as copy:
                        for a in assets:
                            series = result.series[(a.tenant_id, a.id)]
                            for tag, values in series.values.items():
                                quality = series.quality[tag]
                                for i in np.flatnonzero(series.present[tag]):
                                    copy.write_row((
                                        a.tenant_id, a.id, tag, timestamps[i],
                                        round(float(values[i]), 3), int(quality[i]),
                                    ))
                    cur.execute(
                        "INSERT INTO ops.sensor_readings (tenant_id, asset_id, tag, ts, value, quality)"
                        " SELECT tenant_id, asset_id, tag, ts, value, quality FROM readings_stage"
                    )
                    cur.executemany(
                        "INSERT INTO ops.alarms (tenant_id, asset_id, tag, alarm_type, severity,"
                        " raised_at, cleared_at, value, limit_value, message)"
                        " VALUES (%s, %s, %s, %s, %s, %s, %s, %s, %s, %s)",
                        [
                            (al.tenant_id, al.asset_id, al.tag, al.alarm_type, al.severity,
                             al.raised_at, al.cleared_at, al.value, al.limit_value, al.message)
                            for al in result.alarms
                            if al.tenant_id == tenant.id
                        ],
                    )


def tenant_counts(settings: Settings, tenant_ids: list[str]) -> dict[str, dict[str, int]]:
    """Row counts per tenant as seen by ops_reader; `foreign` must always be 0."""
    out: dict[str, dict[str, int]] = {}
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        for tenant_id in tenant_ids:
            with conn.transaction():
                _as_tenant(conn, tenant_id)
                counts = {"foreign": 0}
                for table in COUNT_TABLES:
                    total, foreign = conn.execute(
                        sql.SQL(
                            "SELECT count(*), count(*) FILTER (WHERE tenant_id <> %s) FROM ops.{}"
                        ).format(sql.Identifier(table)),
                        (tenant_id,),
                    ).fetchone()
                    counts[table] = total
                    counts["foreign"] += foreign
                out[tenant_id] = counts
    return out


def write_ground_truth(result: SimResult, path: Path) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("w", encoding="utf-8") as fh:
        for gt in result.ground_truth:
            fh.write(json.dumps(gt.to_json()) + "\n")
