CREATE TABLE ops.tenants (
    id        text PRIMARY KEY,
    name      text NOT NULL,
    timezone  text NOT NULL
);

CREATE TABLE ops.assets (
    tenant_id    text NOT NULL REFERENCES ops.tenants (id),
    id           text NOT NULL,
    name         text NOT NULL,
    asset_model  text NOT NULL,
    site         text NOT NULL,
    synthetic    boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, id)
);

CREATE TABLE ops.sensors (
    tenant_id     text NOT NULL,
    asset_id      text NOT NULL,
    tag           text NOT NULL,
    kind          text NOT NULL
                  CHECK (kind IN ('temperature', 'flow', 'pressure', 'current', 'vibration')),
    description   text NOT NULL,
    unit          text NOT NULL,
    lo_limit      double precision,
    hi_limit      double precision,
    related_tags  text[] NOT NULL DEFAULT '{}',
    PRIMARY KEY (tenant_id, asset_id, tag),
    FOREIGN KEY (tenant_id, asset_id) REFERENCES ops.assets (tenant_id, id)
);

CREATE TABLE ops.alarms (
    id           bigint GENERATED ALWAYS AS IDENTITY PRIMARY KEY,
    tenant_id    text NOT NULL,
    asset_id     text NOT NULL,
    tag          text NOT NULL,
    alarm_type   text NOT NULL,
    severity     text NOT NULL CHECK (severity IN ('low', 'medium', 'high')),
    raised_at    timestamptz NOT NULL,
    cleared_at   timestamptz,
    value        double precision,
    limit_value  double precision,
    message      text NOT NULL,
    synthetic    boolean NOT NULL DEFAULT true,
    FOREIGN KEY (tenant_id, asset_id, tag) REFERENCES ops.sensors (tenant_id, asset_id, tag)
);
CREATE INDEX alarms_lookup ON ops.alarms (tenant_id, asset_id, raised_at);

-- No FK on readings: bulk load speed. Loader writes only tags that exist in ops.sensors.
CREATE TABLE ops.sensor_readings (
    tenant_id  text NOT NULL,
    asset_id   text NOT NULL,
    tag        text NOT NULL,
    ts         timestamptz NOT NULL,
    value      double precision,
    quality    smallint NOT NULL DEFAULT 192,   -- OPC: 192 good, 0 bad
    synthetic  boolean NOT NULL DEFAULT true,
    PRIMARY KEY (tenant_id, asset_id, tag, ts)
);

GRANT SELECT ON ALL TABLES IN SCHEMA ops TO ops_reader;
GRANT SELECT, INSERT, DELETE ON ALL TABLES IN SCHEMA ops TO ops_loader;
GRANT USAGE ON ALL SEQUENCES IN SCHEMA ops TO ops_loader;
