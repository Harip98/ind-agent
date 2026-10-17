# M0 Foundations Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:subagent-driven-development (recommended) or superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** A reproducible skeleton that boots Postgres+pgvector with schemas, roles and row-level security, and loads 30 days of labelled synthetic telemetry, alarms and ground truth for two tenants.

**Architecture:** Postgres runs in Docker Compose. A Python uv project (`services/ai-svc`, package `copilot`) owns the `migrate` and `simgen` CLI commands. Simgen is pure numpy (generate → detect alarms → ground truth) with a separate DB loader that writes per tenant through RLS. A minimal Go module (`services/data-svc`) serves `/healthz` so M1 starts from a working build.

**Tech Stack:** Docker Compose, `pgvector/pgvector:pg16`, Python 3.12 (uv), numpy, psycopg 3, pydantic-settings, typer, tzdata, pytest; Go 1.25 stdlib.

**Spec:** [docs/architecture.md](../architecture.md), milestone M0 in [docs/plan.md](../plan.md).

## Global Constraints

- Python `>=3.12,<3.13`, managed by uv; run everything as `uv run …` from `services/ai-svc`.
- Postgres image `pgvector/pgvector:pg16`, host port **5433**, database `indagent`.
- DB roles: `app_owner` (migrations only), `ops_reader`, `ops_loader`, `kb_reader`, `kb_writer`. Services never connect as `app_owner`.
- `ENABLE` + `FORCE ROW LEVEL SECURITY` on every tenant table; tenant comes from `current_setting('app.tenant_id')`; unset tenant ⇒ zero rows.
- `kb` policies additionally allow `tenant_id IS NULL` (shared documents).
- Every synthetic row carries `synthetic = true`.
- Tenants: `acme` (Asia/Kolkata), `globex` (Europe/Berlin). 10 assets total. **globex has no P-12.**
- 1-minute readings, 30 days by default; deterministic for a given `(end, days, seed)`.
- Fault cause codes: `COOLING_RESTRICTION`, `BEARING_WEAR`, `SENSOR_DRIFT`, `DRY_RUN`, `CAVITATION`, `SENSOR_FAILURE`, `HX_FOULING`, `COOLER_FAN_FAILURE`, plus `NUISANCE_SPIKE`.
- No real customer data; no write paths outside the `simgen`/`ingest`/`migrate` CLIs.

## Review Focus

1. **Tenant set but asking for another tenant's rows** — must return nothing, never an error that reveals rows exist → pinned in Task 3 (`test_reader_sees_only_own_tenant`, `test_loader_cannot_write_other_tenant`).
2. **Connection with no `app.tenant_id` set** — must see zero rows (fail closed) → Task 3 `test_reader_without_tenant_sees_nothing`.
3. **Re-running `migrate` / `simgen`** — must be idempotent (no duplicates, no errors) → Task 3 `test_migrations_are_idempotent`; Task 7 loader deletes the tenant's rows before reloading, verified by running `simgen` twice.
4. **Normal-operation noise crossing limits** — would create alarms not tied to any incident and poison eval ground truth → Task 6 `test_every_alarm_belongs_to_exactly_one_incident`.
5. **`--end` without a timezone / incidents outside the window** — must be a clear error, not silently shifted data → Task 6 `test_naive_end_rejected`, `test_too_few_days_rejected`.

---

## File map

| Path | Responsibility |
|---|---|
| `README.md`, `.env.example`, `docker-compose.yml` | Setup and local infrastructure |
| `db/migrations/001_roles_schemas.sql` | Roles, schemas, extension |
| `db/migrations/002_ops.sql` | Operational tables + grants |
| `db/migrations/003_kb.sql` | Knowledge-base tables + indexes + grants |
| `db/migrations/004_rls.sql` | `current_tenant()` + RLS policies |
| `services/ai-svc/pyproject.toml`, `.python-version` | uv project |
| `src/copilot/config.py` | Settings, repo root, DSNs per role |
| `src/copilot/db/migrate.py` | Migration discovery/apply, role passwords |
| `src/copilot/simgen/catalog.py` | Tenants, asset models, sensors, assets |
| `src/copilot/simgen/signals.py` | Normal-operation series |
| `src/copilot/simgen/faults.py` | Fault profiles and effects |
| `src/copilot/simgen/scenarios.py` | Incident schedule, local→UTC time resolution |
| `src/copilot/simgen/alarms.py` | Limit + bad-quality alarm detection |
| `src/copilot/simgen/generate.py` | Orchestrates generation, ground truth |
| `src/copilot/simgen/loader.py` | DB load per tenant, counts, ground-truth file |
| `src/copilot/cli.py` | `copilot migrate`, `copilot simgen` |
| `services/data-svc/…` | Go module, `/healthz` |

---

### Task 1: Repo scaffolding, Compose and settings

**Files:**
- Create: `README.md`, `.env.example`, `docker-compose.yml`
- Create: `services/ai-svc/pyproject.toml`, `services/ai-svc/.python-version`
- Create: `services/ai-svc/src/copilot/__init__.py`, `services/ai-svc/src/copilot/config.py`
- Test: `services/ai-svc/tests/test_config.py`

**Interfaces:**
- Produces: `copilot.config.REPO_ROOT: Path`, `find_repo_root(start: Path | None = None) -> Path`, `ROLES: tuple[str, ...]`, `Settings` with `password_for(role: str) -> str` and `dsn(role: str) -> str`, `get_settings() -> Settings`.

- [ ] **Step 1: Create infrastructure files**

`docker-compose.yml`:
```yaml
name: ind-agent

services:
  postgres:
    image: pgvector/pgvector:pg16
    environment:
      POSTGRES_USER: app_owner
      POSTGRES_PASSWORD: ${COPILOT_OWNER_PASSWORD:-owner_dev_pw}
      POSTGRES_DB: indagent
    ports:
      - "5433:5432"
    volumes:
      - pgdata:/var/lib/postgresql/data
    healthcheck:
      test: ["CMD-SHELL", "pg_isready -U app_owner -d indagent"]
      interval: 5s
      timeout: 3s
      retries: 20

volumes:
  pgdata:
```

`.env.example`:
```
# Copy to .env. Development-only credentials.
COPILOT_PG_HOST=localhost
COPILOT_PG_PORT=5433
COPILOT_PG_DB=indagent
COPILOT_OWNER_PASSWORD=owner_dev_pw
COPILOT_OPS_READER_PASSWORD=ops_reader_dev_pw
COPILOT_OPS_LOADER_PASSWORD=ops_loader_dev_pw
COPILOT_KB_READER_PASSWORD=kb_reader_dev_pw
COPILOT_KB_WRITER_PASSWORD=kb_writer_dev_pw
```

`README.md`:
````markdown
# ind-agent — Industrial Troubleshooting Copilot (prototype)

Read-only assistant that investigates equipment incidents from maintenance documents, alarm
history and telemetry. **All telemetry and alarms in this repo are synthetic.**

Design: [docs/architecture.md](docs/architecture.md) · Plan: [docs/plan.md](docs/plan.md)

## Prerequisites (Windows)
- Docker Desktop (running)
- [uv](https://docs.astral.sh/uv/) — installs Python 3.12 for the project
- Go 1.25+
- Ollama (used from M2 onwards)

## Setup
```bash
cp .env.example .env
docker compose up -d
cd services/ai-svc
uv sync
uv run copilot migrate
uv run copilot simgen          # 30 days of synthetic data, ends now
```

## Tests
```bash
cd services/ai-svc
uv run pytest                  # unit tests
uv run pytest -m integration   # needs docker compose Postgres + migrate
cd ../data-svc && go test ./...
```
````

- [ ] **Step 2: Create the uv project**

`services/ai-svc/.python-version`:
```
3.12
```

`services/ai-svc/pyproject.toml`:
```toml
[project]
name = "copilot"
version = "0.1.0"
description = "Industrial troubleshooting copilot - AI service and tooling"
requires-python = ">=3.12,<3.13"
dependencies = [
    "numpy>=2.1",
    "psycopg[binary]>=3.2",
    "pydantic-settings>=2.6",
    "typer>=0.15",
    "tzdata>=2024.2",
]

[project.scripts]
copilot = "copilot.cli:app"

[dependency-groups]
dev = ["pytest>=8.3", "ruff>=0.8"]

[build-system]
requires = ["hatchling"]
build-backend = "hatchling.build"

[tool.hatch.build.targets.wheel]
packages = ["src/copilot"]

[tool.pytest.ini_options]
testpaths = ["tests"]
addopts = ["-m", "not integration"]
markers = ["integration: needs the docker compose Postgres (run with -m integration)"]

[tool.ruff]
line-length = 100
target-version = "py312"
```

`services/ai-svc/src/copilot/__init__.py`:
```python
"""Industrial troubleshooting copilot."""
```

- [ ] **Step 3: Write the failing test**

`services/ai-svc/tests/test_config.py`:
```python
from pathlib import Path

import pytest

from copilot.config import REPO_ROOT, ROLES, Settings, find_repo_root


def test_repo_root_contains_compose_file():
    assert (REPO_ROOT / "docker-compose.yml").is_file()


def test_find_repo_root_raises_outside_repo(tmp_path: Path):
    with pytest.raises(RuntimeError):
        find_repo_root(tmp_path)


def test_dsn_uses_role_specific_password():
    s = Settings(pg_host="db", pg_port=1234, pg_db="x", ops_reader_password="secret")
    assert s.dsn("ops_reader") == "host=db port=1234 dbname=x user=ops_reader password=secret"


def test_owner_password_maps_to_app_owner():
    s = Settings(owner_password="own")
    assert s.password_for("app_owner") == "own"


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        Settings().dsn("postgres")


def test_roles_are_the_five_documented_roles():
    assert ROLES == ("app_owner", "ops_reader", "ops_loader", "kb_reader", "kb_writer")
```

- [ ] **Step 4: Run test to verify it fails**

Run: `cd services/ai-svc && uv sync && uv run pytest tests/test_config.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copilot.config'`

- [ ] **Step 5: Implement settings**

`services/ai-svc/src/copilot/config.py`:
```python
"""Runtime settings, read from COPILOT_* environment variables and the repo-root .env."""

from functools import lru_cache
from pathlib import Path

from pydantic_settings import BaseSettings, SettingsConfigDict


def find_repo_root(start: Path | None = None) -> Path:
    """Walk up from `start` to the directory holding docker-compose.yml."""
    here = (start or Path(__file__)).resolve()
    for candidate in (here, *here.parents):
        if (candidate / "docker-compose.yml").is_file():
            return candidate
    raise RuntimeError(f"no docker-compose.yml above {here}")


REPO_ROOT = find_repo_root()

ROLES = ("app_owner", "ops_reader", "ops_loader", "kb_reader", "kb_writer")


class Settings(BaseSettings):
    model_config = SettingsConfigDict(
        env_prefix="COPILOT_", env_file=REPO_ROOT / ".env", extra="ignore"
    )

    pg_host: str = "localhost"
    pg_port: int = 5433
    pg_db: str = "indagent"
    owner_password: str = "owner_dev_pw"
    ops_reader_password: str = "ops_reader_dev_pw"
    ops_loader_password: str = "ops_loader_dev_pw"
    kb_reader_password: str = "kb_reader_dev_pw"
    kb_writer_password: str = "kb_writer_dev_pw"

    def password_for(self, role: str) -> str:
        if role not in ROLES:
            raise ValueError(f"unknown database role: {role}")
        field = "owner_password" if role == "app_owner" else f"{role}_password"
        return getattr(self, field)

    def dsn(self, role: str) -> str:
        return (
            f"host={self.pg_host} port={self.pg_port} dbname={self.pg_db} "
            f"user={role} password={self.password_for(role)}"
        )


@lru_cache
def get_settings() -> Settings:
    return Settings()
```

- [ ] **Step 6: Run test to verify it passes**

Run: `uv run pytest tests/test_config.py -v`
Expected: 6 passed

- [ ] **Step 7: Commit**

```bash
git add README.md .env.example docker-compose.yml services/ai-svc
git commit -m "chore: scaffold compose, ai-svc uv project and settings"
```

---

### Task 2: Migrations and the migration runner

**Files:**
- Create: `db/migrations/001_roles_schemas.sql`, `002_ops.sql`, `003_kb.sql`, `004_rls.sql`
- Create: `services/ai-svc/src/copilot/db/__init__.py`, `services/ai-svc/src/copilot/db/migrate.py`
- Create: `services/ai-svc/src/copilot/cli.py`
- Test: `services/ai-svc/tests/db/test_migrate.py`

**Interfaces:**
- Consumes: `Settings.dsn`, `Settings.password_for`, `REPO_ROOT` (Task 1).
- Produces: `MIGRATIONS_DIR: Path`, `discover(migrations_dir: Path) -> list[tuple[str, Path]]`, `apply_migrations(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]` (conn must be autocommit), `set_role_passwords(conn, settings) -> None`; Typer `app` in `copilot.cli` with command `migrate`.

- [ ] **Step 1: Write the SQL migrations**

`db/migrations/001_roles_schemas.sql`:
```sql
DO $$
DECLARE
    r text;
BEGIN
    FOREACH r IN ARRAY ARRAY['ops_reader', 'ops_loader', 'kb_reader', 'kb_writer'] LOOP
        IF NOT EXISTS (SELECT 1 FROM pg_roles WHERE rolname = r) THEN
            EXECUTE format('CREATE ROLE %I LOGIN NOSUPERUSER NOBYPASSRLS', r);
        END IF;
    END LOOP;
END $$;

CREATE EXTENSION IF NOT EXISTS vector;

CREATE SCHEMA IF NOT EXISTS ops;
CREATE SCHEMA IF NOT EXISTS kb;
REVOKE ALL ON SCHEMA ops FROM PUBLIC;
REVOKE ALL ON SCHEMA kb FROM PUBLIC;
GRANT USAGE ON SCHEMA ops TO ops_reader, ops_loader;
GRANT USAGE ON SCHEMA kb TO kb_reader, kb_writer;
```

`db/migrations/002_ops.sql`:
```sql
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

-- No FK on readings: bulk COPY speed. Loader writes only tags that exist in ops.sensors.
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
```

`db/migrations/003_kb.sql`:
```sql
CREATE TABLE kb.documents (
    id         text PRIMARY KEY,
    tenant_id  text,                -- NULL = shared with all tenants
    title      text NOT NULL,
    doc_type   text NOT NULL
);

CREATE TABLE kb.doc_versions (
    doc_id          text NOT NULL REFERENCES kb.documents (id),
    version         integer NOT NULL,
    tenant_id       text,
    effective_date  date NOT NULL,
    superseded_by   integer,
    is_current      boolean NOT NULL,
    source_path     text NOT NULL,
    sha256          text NOT NULL,
    license         text NOT NULL,
    PRIMARY KEY (doc_id, version)
);

CREATE TABLE kb.sections (
    id          text PRIMARY KEY,   -- e.g. SOP-PUMP-07@v3#4.2
    doc_id      text NOT NULL,
    version     integer NOT NULL,
    tenant_id   text,
    section_no  text NOT NULL,
    heading     text NOT NULL,
    text        text NOT NULL,
    FOREIGN KEY (doc_id, version) REFERENCES kb.doc_versions (doc_id, version)
);

CREATE TABLE kb.chunks (
    id            text PRIMARY KEY,
    section_id    text NOT NULL REFERENCES kb.sections (id),
    tenant_id     text,
    asset_models  text[] NOT NULL,
    is_current    boolean NOT NULL,
    content       text NOT NULL,
    embedding     vector(768),
    tsv           tsvector GENERATED ALWAYS AS (to_tsvector('english', content)) STORED
);
CREATE INDEX chunks_embedding_hnsw ON kb.chunks USING hnsw (embedding vector_cosine_ops);
CREATE INDEX chunks_tsv_gin ON kb.chunks USING gin (tsv);
CREATE INDEX chunks_filter ON kb.chunks (tenant_id, is_current);

GRANT SELECT ON ALL TABLES IN SCHEMA kb TO kb_reader;
GRANT SELECT, INSERT, UPDATE, DELETE ON ALL TABLES IN SCHEMA kb TO kb_writer;
```

`db/migrations/004_rls.sql`:
```sql
-- Tenant of the current transaction; NULL when unset, so policies fail closed.
CREATE FUNCTION public.current_tenant() RETURNS text
    LANGUAGE sql STABLE
    AS $$ SELECT nullif(current_setting('app.tenant_id', true), '') $$;

ALTER TABLE ops.tenants ENABLE ROW LEVEL SECURITY;
ALTER TABLE ops.tenants FORCE ROW LEVEL SECURITY;
CREATE POLICY tenant_isolation ON ops.tenants
    USING (id = public.current_tenant())
    WITH CHECK (id = public.current_tenant());

DO $$
DECLARE
    t text;
BEGIN
    FOREACH t IN ARRAY ARRAY['assets', 'sensors', 'alarms', 'sensor_readings'] LOOP
        EXECUTE format('ALTER TABLE ops.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE ops.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_isolation ON ops.%I '
            'USING (tenant_id = public.current_tenant()) '
            'WITH CHECK (tenant_id = public.current_tenant())', t);
    END LOOP;

    FOREACH t IN ARRAY ARRAY['documents', 'doc_versions', 'sections', 'chunks'] LOOP
        EXECUTE format('ALTER TABLE kb.%I ENABLE ROW LEVEL SECURITY', t);
        EXECUTE format('ALTER TABLE kb.%I FORCE ROW LEVEL SECURITY', t);
        EXECUTE format(
            'CREATE POLICY tenant_or_shared ON kb.%I '
            'USING (tenant_id IS NULL OR tenant_id = public.current_tenant()) '
            'WITH CHECK (tenant_id IS NULL OR tenant_id = public.current_tenant())', t);
    END LOOP;
END $$;
```

- [ ] **Step 2: Write the failing test**

`services/ai-svc/tests/db/test_migrate.py`:
```python
from pathlib import Path

import pytest

from copilot.db.migrate import MIGRATIONS_DIR, discover


def test_discover_orders_by_number(tmp_path: Path):
    (tmp_path / "002_b.sql").write_text("select 2;")
    (tmp_path / "001_a.sql").write_text("select 1;")
    (tmp_path / "notes.txt").write_text("ignored")
    assert [v for v, _ in discover(tmp_path)] == ["001_a", "002_b"]


def test_discover_rejects_badly_named_sql(tmp_path: Path):
    (tmp_path / "add_table.sql").write_text("select 1;")
    with pytest.raises(ValueError, match="add_table.sql"):
        discover(tmp_path)


def test_repo_migrations_are_well_formed():
    versions = [v for v, _ in discover(MIGRATIONS_DIR)]
    assert versions == ["001_roles_schemas", "002_ops", "003_kb", "004_rls"]
```

- [ ] **Step 3: Run test to verify it fails**

Run: `uv run pytest tests/db/test_migrate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copilot.db'`

- [ ] **Step 4: Implement the runner and the CLI command**

`services/ai-svc/src/copilot/db/__init__.py`:
```python
```

`services/ai-svc/src/copilot/db/migrate.py`:
```python
"""Apply numbered SQL migrations from db/migrations exactly once each."""

import re
from pathlib import Path

import psycopg
from psycopg import sql

from copilot.config import REPO_ROOT, Settings

MIGRATIONS_DIR = REPO_ROOT / "db" / "migrations"
_NAME = re.compile(r"^\d{3}_[a-z0-9_]+\.sql$")
_SERVICE_ROLES = ("ops_reader", "ops_loader", "kb_reader", "kb_writer")


def discover(migrations_dir: Path) -> list[tuple[str, Path]]:
    found = []
    for path in sorted(migrations_dir.iterdir()):
        if path.suffix != ".sql":
            continue
        if not _NAME.match(path.name):
            raise ValueError(f"bad migration filename: {path.name}")
        found.append((path.stem, path))
    return found


def apply_migrations(conn: psycopg.Connection, migrations_dir: Path = MIGRATIONS_DIR) -> list[str]:
    """Apply pending migrations, each in its own transaction. `conn` must be autocommit."""
    conn.execute(
        "CREATE TABLE IF NOT EXISTS public.schema_migrations ("
        " version text PRIMARY KEY, applied_at timestamptz NOT NULL DEFAULT now())"
    )
    applied = {row[0] for row in conn.execute("SELECT version FROM public.schema_migrations")}
    newly_applied = []
    for version, path in discover(migrations_dir):
        if version in applied:
            continue
        with conn.transaction():
            conn.execute(path.read_text(encoding="utf-8"))
            conn.execute("INSERT INTO public.schema_migrations (version) VALUES (%s)", (version,))
        newly_applied.append(version)
    return newly_applied


def set_role_passwords(conn: psycopg.Connection, settings: Settings) -> None:
    for role in _SERVICE_ROLES:
        conn.execute(
            sql.SQL("ALTER ROLE {} PASSWORD {}").format(
                sql.Identifier(role), sql.Literal(settings.password_for(role))
            )
        )
```

`services/ai-svc/src/copilot/cli.py`:
```python
"""`copilot` command-line entry point."""

import psycopg
import typer

from copilot.config import get_settings
from copilot.db.migrate import apply_migrations, set_role_passwords

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def migrate() -> None:
    """Apply pending database migrations and set service role passwords."""
    settings = get_settings()
    with psycopg.connect(settings.dsn("app_owner"), autocommit=True) as conn:
        applied = apply_migrations(conn)
        set_role_passwords(conn, settings)
    typer.echo(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")
```

- [ ] **Step 5: Run test to verify it passes**

Run: `uv run pytest tests/db/test_migrate.py -v`
Expected: 3 passed

- [ ] **Step 6: Commit**

```bash
git add db services/ai-svc/src/copilot/db services/ai-svc/src/copilot/cli.py services/ai-svc/tests/db
git commit -m "feat(db): schemas, roles, RLS migrations and migrate command"
```

---

### Task 3: Bring up Postgres and pin tenant isolation with integration tests

**Files:**
- Create: `services/ai-svc/tests/integration/__init__.py` (empty), `services/ai-svc/tests/integration/conftest.py`, `services/ai-svc/tests/integration/test_rls.py`

**Interfaces:**
- Consumes: `get_settings`, `apply_migrations`, `MIGRATIONS_DIR`, `copilot migrate`.
- Produces: pytest fixtures `settings` (session) and helper `as_tenant(conn, tenant_id)` in `tests/integration/conftest.py`.

- [ ] **Step 1: Start Docker and Postgres, apply migrations**

Run (Docker Desktop must be running; start it from the Start menu if `docker info` fails):
```bash
cp .env.example .env
docker compose up -d --wait
cd services/ai-svc && uv run copilot migrate
```
Expected: `applied: 001_roles_schemas, 002_ops, 003_kb, 004_rls`. Running `uv run copilot migrate` again prints `applied: nothing (up to date)`.

- [ ] **Step 2: Write the integration tests**

`services/ai-svc/tests/integration/conftest.py`:
```python
import psycopg
import pytest

from copilot.config import Settings, get_settings


def as_tenant(conn: psycopg.Connection, tenant_id: str) -> None:
    """Scope the current transaction to `tenant_id`."""
    conn.execute("SELECT set_config('app.tenant_id', %s, true)", (tenant_id,))


@pytest.fixture(scope="session")
def settings() -> Settings:
    s = get_settings()
    try:
        psycopg.connect(s.dsn("app_owner"), connect_timeout=3).close()
    except psycopg.OperationalError as exc:
        pytest.fail(f"Postgres not reachable - run `docker compose up -d` and `copilot migrate`: {exc}")
    return s
```

`services/ai-svc/tests/integration/test_rls.py`:
```python
import psycopg
import pytest

from copilot.db.migrate import apply_migrations

from .conftest import as_tenant

pytestmark = pytest.mark.integration

TENANTS = ("itest_a", "itest_b")


def _cleanup(conn: psycopg.Connection, tenant_id: str) -> None:
    conn.execute("DELETE FROM ops.assets WHERE tenant_id = %s", (tenant_id,))
    conn.execute("DELETE FROM ops.tenants WHERE id = %s", (tenant_id,))


@pytest.fixture(scope="module")
def seeded(settings):
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        for tid in TENANTS:
            with conn.transaction():
                as_tenant(conn, tid)
                _cleanup(conn, tid)
                conn.execute(
                    "INSERT INTO ops.tenants (id, name, timezone) VALUES (%s, %s, 'UTC')", (tid, tid)
                )
                conn.execute(
                    "INSERT INTO ops.assets (tenant_id, id, name, asset_model, site)"
                    " VALUES (%s, 'P-1', 'Pump 1', 'CP-200', 'Test')",
                    (tid,),
                )
    with psycopg.connect(settings.dsn("kb_writer")) as conn:
        with conn.transaction():
            as_tenant(conn, "itest_a")
            conn.execute("DELETE FROM kb.documents WHERE id LIKE %s", ("ITEST-%",))
            conn.execute(
                "INSERT INTO kb.documents (id, tenant_id, title, doc_type) VALUES"
                " ('ITEST-SHARED', NULL, 'Shared', 'sop'),"
                " ('ITEST-A', 'itest_a', 'Tenant A only', 'sop')"
            )
    yield
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        for tid in TENANTS:
            with conn.transaction():
                as_tenant(conn, tid)
                _cleanup(conn, tid)
    with psycopg.connect(settings.dsn("kb_writer")) as conn:
        with conn.transaction():
            as_tenant(conn, "itest_a")
            conn.execute("DELETE FROM kb.documents WHERE id LIKE %s", ("ITEST-%",))


def test_reader_sees_only_own_tenant(settings, seeded):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        as_tenant(conn, "itest_a")
        rows = conn.execute(
            "SELECT DISTINCT tenant_id FROM ops.assets WHERE tenant_id LIKE 'itest_%'"
        ).fetchall()
        explicit = conn.execute("SELECT count(*) FROM ops.assets WHERE tenant_id = 'itest_b'")
        assert explicit.fetchone()[0] == 0
    assert rows == [("itest_a",)]


def test_reader_without_tenant_sees_nothing(settings, seeded):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        assert conn.execute("SELECT count(*) FROM ops.assets").fetchone()[0] == 0
        assert conn.execute("SELECT count(*) FROM ops.tenants").fetchone()[0] == 0


def test_reader_cannot_write(settings, seeded):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        as_tenant(conn, "itest_a")
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("INSERT INTO ops.tenants (id, name, timezone) VALUES ('itest_a', 'x', 'UTC')")


def test_loader_cannot_write_other_tenant(settings, seeded):
    with psycopg.connect(settings.dsn("ops_loader")) as conn:
        as_tenant(conn, "itest_a")
        with pytest.raises(psycopg.errors.InsufficientPrivilege, match="row-level security"):
            conn.execute(
                "INSERT INTO ops.assets (tenant_id, id, name, asset_model, site)"
                " VALUES ('itest_b', 'P-9', 'Pump 9', 'CP-200', 'Test')"
            )


def test_kb_reader_has_no_access_to_ops(settings):
    with psycopg.connect(settings.dsn("kb_reader")) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM ops.assets LIMIT 1")


def test_ops_reader_has_no_access_to_kb(settings):
    with psycopg.connect(settings.dsn("ops_reader")) as conn:
        with pytest.raises(psycopg.errors.InsufficientPrivilege):
            conn.execute("SELECT 1 FROM kb.documents LIMIT 1")


def test_kb_shared_docs_visible_tenant_docs_isolated(settings, seeded):
    with psycopg.connect(settings.dsn("kb_reader")) as conn:
        as_tenant(conn, "itest_b")
        ids = {r[0] for r in conn.execute("SELECT id FROM kb.documents WHERE id LIKE %s", ("ITEST-%",))}
    assert ids == {"ITEST-SHARED"}


def test_migrations_are_idempotent(settings):
    with psycopg.connect(settings.dsn("app_owner"), autocommit=True) as conn:
        assert apply_migrations(conn) == []
```

- [ ] **Step 3: Run the integration tests**

Run: `uv run pytest -m integration -v`
Expected: 8 passed. If any test fails, the RLS/grant SQL in Task 2 is wrong — fix the migration with a new numbered file (never edit an applied one after M0 ships; during M0 it is acceptable to `docker compose down -v` and re-run `migrate`).

- [ ] **Step 4: Commit**

```bash
git add services/ai-svc/tests/integration
git commit -m "test(db): integration tests for tenant isolation and role grants"
```

---

### Task 4: Simgen catalog and normal-operation signals

**Files:**
- Create: `services/ai-svc/src/copilot/simgen/__init__.py`, `catalog.py`, `signals.py`
- Test: `services/ai-svc/tests/simgen/test_signals.py`

**Interfaces:**
- Produces: dataclasses `SensorSpec`, `AssetModel` (with `.sensor(tag) -> SensorSpec`), `Tenant`, `Asset`; constants `MODELS: dict[str, AssetModel]`, `TENANTS: tuple[Tenant, ...]`, `ASSETS: tuple[Asset, ...]`; `signals.GOOD = 192`, `signals.BAD = 0`, `AssetSeries(values, quality, present)` (dicts tag → `np.ndarray`), `baseline(spec, start, n, rng) -> np.ndarray`, `normal_series(model, start, n, rng) -> AssetSeries`.

- [ ] **Step 1: Write the failing test**

`services/ai-svc/tests/simgen/test_signals.py`:
```python
from datetime import UTC, datetime

import numpy as np

from copilot.simgen.catalog import ASSETS, MODELS, TENANTS
from copilot.simgen.signals import GOOD, baseline, normal_series

START = datetime(2026, 9, 1, tzinfo=UTC)
N = 14 * 1440


def test_catalog_has_two_tenants_ten_assets_and_no_globex_p12():
    assert {t.id for t in TENANTS} == {"acme", "globex"}
    assert len(ASSETS) == 10
    assert ("globex", "P-12") not in {(a.tenant_id, a.id) for a in ASSETS}
    assert all(a.model in MODELS for a in ASSETS)


def test_related_tags_exist_on_the_same_model():
    for model in MODELS.values():
        tags = {s.tag for s in model.sensors}
        for s in model.sensors:
            assert set(s.related) <= tags, (model.code, s.tag)


def test_baseline_is_deterministic_for_a_seed():
    spec = MODELS["CP-200"].sensor("TT-101")
    a = baseline(spec, START, N, np.random.default_rng(1))
    b = baseline(spec, START, N, np.random.default_rng(1))
    assert np.array_equal(a, b)


def test_baseline_mean_and_noise_match_spec():
    spec = MODELS["CP-200"].sensor("FT-101")
    v = baseline(spec, START, N, np.random.default_rng(2))
    assert abs(v.mean() - spec.baseline) < 0.5
    assert v.std() < spec.noise * 3 + spec.daily_amplitude


def test_normal_operation_never_crosses_limits():
    rng = np.random.default_rng(3)
    for model in MODELS.values():
        s = normal_series(model, START, N, rng)
        for spec in model.sensors:
            v = s.values[spec.tag]
            if spec.hi_limit is not None:
                assert v.max() < spec.hi_limit, (model.code, spec.tag)
            if spec.lo_limit is not None:
                assert v.min() > spec.lo_limit, (model.code, spec.tag)
            assert (s.quality[spec.tag] == GOOD).all()
            assert s.present[spec.tag].all()
```

`services/ai-svc/tests/simgen/__init__.py` and `services/ai-svc/tests/db/__init__.py` stay absent (pytest rootdir-relative imports are fine with unique test file names).

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/simgen/test_signals.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copilot.simgen'`

- [ ] **Step 3: Implement catalog and signals**

`services/ai-svc/src/copilot/simgen/__init__.py`:
```python
"""Synthetic telemetry, alarms and ground truth. Everything produced here is labelled synthetic."""
```

`services/ai-svc/src/copilot/simgen/catalog.py`:
```python
"""Static catalog of synthetic tenants, asset models, sensors and assets."""

from dataclasses import dataclass


@dataclass(frozen=True)
class SensorSpec:
    tag: str
    kind: str  # temperature | flow | pressure | current | vibration
    description: str
    unit: str
    baseline: float
    noise: float  # standard deviation of the AR(1) noise
    daily_amplitude: float
    lo_limit: float | None
    hi_limit: float | None
    related: tuple[str, ...]


@dataclass(frozen=True)
class AssetModel:
    code: str
    description: str
    sensors: tuple[SensorSpec, ...]

    def sensor(self, tag: str) -> SensorSpec:
        for spec in self.sensors:
            if spec.tag == tag:
                return spec
        raise KeyError(f"{self.code} has no sensor {tag}")


@dataclass(frozen=True)
class Tenant:
    id: str
    name: str
    timezone: str


@dataclass(frozen=True)
class Asset:
    tenant_id: str
    id: str
    name: str
    model: str
    site: str


CP_200 = AssetModel("CP-200", "Centrifugal process pump, 75 kW", (
    SensorSpec("TT-101", "temperature", "Pump bearing temperature", "degC",
               62.0, 0.4, 2.0, None, 85.0, ("TT-102", "FT-101", "IT-101", "VT-101")),
    SensorSpec("TT-102", "temperature", "Bearing cooling water return temperature", "degC",
               28.0, 0.3, 1.5, None, 38.0, ("TT-101", "FT-101")),
    SensorSpec("FT-101", "flow", "Discharge flow", "m3/h",
               120.0, 1.5, 5.0, 60.0, None, ("PT-101", "IT-101")),
    SensorSpec("PT-101", "pressure", "Discharge pressure", "bar",
               6.5, 0.05, 0.1, 3.0, 9.0, ("FT-101",)),
    SensorSpec("IT-101", "current", "Motor current", "A",
               42.0, 0.5, 1.0, None, 55.0, ("FT-101", "TT-101")),
    SensorSpec("VT-101", "vibration", "Bearing vibration velocity", "mm/s",
               2.2, 0.1, 0.1, None, 7.1, ("TT-101",)),
))

CL_50 = AssetModel("CL-50", "Closed cooling water loop with plate heat exchanger", (
    SensorSpec("TT-201", "temperature", "Cooling water supply temperature", "degC",
               24.0, 0.2, 1.5, None, 32.0, ("TT-202", "FT-201")),
    SensorSpec("TT-202", "temperature", "Cooling water return temperature", "degC",
               31.0, 0.2, 1.5, None, 40.0, ("TT-201", "FT-201")),
    SensorSpec("FT-201", "flow", "Loop flow", "m3/h",
               300.0, 3.0, 0.0, 200.0, None, ("PT-201", "TT-201")),
    SensorSpec("PT-201", "pressure", "Loop pressure", "bar",
               3.2, 0.03, 0.0, 2.0, None, ("FT-201",)),
))

AC_75 = AssetModel("AC-75", "Oil-injected rotary screw air compressor, 75 kW", (
    SensorSpec("TT-301", "temperature", "Compressor discharge temperature", "degC",
               88.0, 0.5, 2.0, None, 105.0, ("IT-301", "PT-301")),
    SensorSpec("PT-301", "pressure", "Discharge pressure", "bar",
               7.5, 0.05, 0.1, 6.0, 8.6, ("IT-301", "TT-301")),
    SensorSpec("IT-301", "current", "Motor current", "A",
               120.0, 1.2, 3.0, None, 140.0, ("PT-301", "TT-301")),
    SensorSpec("VT-301", "vibration", "Airend vibration velocity", "mm/s",
               2.8, 0.1, 0.1, None, 7.1, ("TT-301",)),
))

MODELS: dict[str, AssetModel] = {m.code: m for m in (CP_200, CL_50, AC_75)}

TENANTS: tuple[Tenant, ...] = (
    Tenant("acme", "Acme Process Plant", "Asia/Kolkata"),
    Tenant("globex", "Globex Utilities", "Europe/Berlin"),
)

ASSETS: tuple[Asset, ...] = (
    Asset("acme", "P-10", "Pump 10", "CP-200", "Plant A"),
    Asset("acme", "P-11", "Pump 11", "CP-200", "Plant A"),
    Asset("acme", "P-12", "Pump 12", "CP-200", "Plant A"),
    Asset("acme", "CL-1", "Cooling Loop 1", "CL-50", "Plant A"),
    Asset("acme", "AC-1", "Air Compressor 1", "AC-75", "Plant A"),
    Asset("globex", "P-20", "Pump 20", "CP-200", "North Station"),
    Asset("globex", "P-21", "Pump 21", "CP-200", "North Station"),
    Asset("globex", "CL-2", "Cooling Loop 2", "CL-50", "North Station"),
    Asset("globex", "AC-2", "Air Compressor 2", "AC-75", "North Station"),
    Asset("globex", "AC-3", "Air Compressor 3", "AC-75", "South Station"),
)
```

`services/ai-svc/src/copilot/simgen/signals.py`:
```python
"""Normal-operation sensor series: level + daily cycle + AR(1) noise, one sample per minute."""

from dataclasses import dataclass
from datetime import datetime

import numpy as np

from copilot.simgen.catalog import AssetModel, SensorSpec

GOOD = 192  # OPC quality codes
BAD = 0
AR_COEF = 0.9


@dataclass
class AssetSeries:
    values: dict[str, np.ndarray]
    quality: dict[str, np.ndarray]
    present: dict[str, np.ndarray]  # False = no row stored (data gap)


def baseline(spec: SensorSpec, start: datetime, n: int, rng: np.random.Generator) -> np.ndarray:
    minute_of_day = (start.hour * 60 + start.minute + np.arange(n)) % 1440
    daily = spec.daily_amplitude * np.sin(2 * np.pi * (minute_of_day - 360) / 1440)
    shocks = rng.normal(0.0, spec.noise * np.sqrt(1 - AR_COEF**2), n)
    noise = np.empty(n)
    acc = 0.0
    for i in range(n):
        acc = AR_COEF * acc + shocks[i]
        noise[i] = acc
    return spec.baseline + daily + noise


def normal_series(
    model: AssetModel, start: datetime, n: int, rng: np.random.Generator
) -> AssetSeries:
    values = {spec.tag: baseline(spec, start, n, rng) for spec in model.sensors}
    return AssetSeries(
        values=values,
        quality={tag: np.full(n, GOOD, dtype=np.int16) for tag in values},
        present={tag: np.ones(n, dtype=bool) for tag in values},
    )
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/simgen/test_signals.py -v`
Expected: 5 passed

- [ ] **Step 5: Commit**

```bash
git add services/ai-svc/src/copilot/simgen services/ai-svc/tests/simgen
git commit -m "feat(simgen): asset catalog and normal-operation signals"
```

---

### Task 5: Fault profiles and incident schedule

**Files:**
- Create: `services/ai-svc/src/copilot/simgen/faults.py`, `services/ai-svc/src/copilot/simgen/scenarios.py`
- Test: `services/ai-svc/tests/simgen/test_faults.py`

**Interfaces:**
- Consumes: `AssetSeries`, `AssetModel`, `MODELS`, `normal_series` (Task 4).
- Produces: `RECOVERY_MINUTES = 30`, `activation(n, i0, ramp_n, hold_n, recovery_n=30) -> np.ndarray`, `FaultProfile(cause_code, asset_model, ramp, hold, primary_tag, fn)`, `PROFILES: dict[str, FaultProfile]`, `apply_fault(series, model, profile, i0, tag=None) -> None`; `Incident(incident_id, tenant_id, asset_id, cause_code, days_ago, local_start, tag=None)`, `INCIDENTS: tuple[Incident, ...]`, `resolve_start(incident, timezone, end) -> datetime`.

- [ ] **Step 1: Write the failing test**

`services/ai-svc/tests/simgen/test_faults.py`:
```python
from datetime import UTC, datetime

import numpy as np
import pytest

from copilot.simgen.catalog import ASSETS, MODELS
from copilot.simgen.faults import PROFILES, activation, apply_fault
from copilot.simgen.scenarios import INCIDENTS, resolve_start
from copilot.simgen.signals import normal_series

START = datetime(2026, 9, 1, tzinfo=UTC)
N = 6 * 1440
I0 = 1440


def _series(model_code: str):
    return normal_series(MODELS[model_code], START, N, np.random.default_rng(0))


def test_activation_ramps_holds_and_recovers():
    r = activation(100, i0=10, ramp_n=10, hold_n=5, recovery_n=10)
    assert r[9] == 0.0
    assert r[19] == pytest.approx(1.0)
    assert (r[20:25] == 1.0).all()
    assert r[34] == pytest.approx(0.0)
    assert (r[35:] == 0.0).all()


def test_activation_with_zero_ramp_is_a_step():
    r = activation(10, i0=2, ramp_n=0, hold_n=3, recovery_n=1)
    assert list(r[:6]) == [0, 0, 1, 1, 1, 0]


def test_cooling_restriction_heats_bearing_and_cooling_water_but_not_flow():
    s, ref = _series("CP-200"), _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["COOLING_RESTRICTION"], I0)
    peak = I0 + 4 * 60 + 30
    assert s.values["TT-101"][peak] > 85
    assert s.values["TT-102"][peak] > 38
    assert np.array_equal(s.values["FT-101"], ref.values["FT-101"])
    assert np.array_equal(s.values["IT-101"], ref.values["IT-101"])


def test_sensor_drift_moves_only_the_drifting_sensor():
    s, ref = _series("CP-200"), _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["SENSOR_DRIFT"], I0)
    assert s.values["TT-101"][I0 + 48 * 60] > 85
    for tag in ("TT-102", "FT-101", "PT-101", "IT-101", "VT-101"):
        assert np.array_equal(s.values[tag], ref.values[tag]), tag


def test_dry_run_collapses_flow_and_pressure():
    s = _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["DRY_RUN"], I0)
    mid = I0 + 60
    assert s.values["FT-101"][mid] < 60
    assert s.values["PT-101"][mid] < 3.0
    assert s.values["TT-101"][I0 + 60] > 85


def test_sensor_failure_flatlines_then_drops_rows():
    s = _series("CP-200")
    apply_fault(s, MODELS["CP-200"], PROFILES["SENSOR_FAILURE"], I0)
    flat = s.values["TT-101"][I0 : I0 + 180]
    assert np.all(flat == flat[0])
    assert not s.present["TT-101"][I0 + 180 : I0 + 240].any()
    assert s.present["TT-101"][I0 + 240]


def test_nuisance_spike_is_two_minutes_over_the_limit():
    s = _series("AC-75")
    apply_fault(s, MODELS["AC-75"], PROFILES["NUISANCE_SPIKE"], I0, tag="PT-301")
    v = s.values["PT-301"]
    assert (v[I0 : I0 + 2] > 8.6).all()
    assert v[I0 + 2] < 8.6


def test_fault_on_wrong_model_rejected():
    with pytest.raises(ValueError, match="CL-50"):
        apply_fault(_series("CL-50"), MODELS["CL-50"], PROFILES["DRY_RUN"], I0)


def test_every_cause_code_is_scheduled_and_assets_exist():
    assert {i.cause_code for i in INCIDENTS} == set(PROFILES)
    known = {(a.tenant_id, a.id) for a in ASSETS}
    assert all((i.tenant_id, i.asset_id) in known for i in INCIDENTS)
    assert len({i.incident_id for i in INCIDENTS}) == len(INCIDENTS)


def test_demo_incident_is_yesterday_morning_in_acme_local_time():
    inc = next(i for i in INCIDENTS if i.incident_id == "INC-01")
    end = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)  # 13:30 in Asia/Kolkata
    assert resolve_start(inc, "Asia/Kolkata", end) == datetime(2026, 10, 16, 0, 30, tzinfo=UTC)
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/simgen/test_faults.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copilot.simgen.faults'`

- [ ] **Step 3: Implement faults and scenarios**

`services/ai-svc/src/copilot/simgen/faults.py`:
```python
"""Fault profiles: how each root cause changes an asset's sensor series."""

from collections.abc import Callable
from dataclasses import dataclass
from datetime import timedelta

import numpy as np

from copilot.simgen.catalog import AssetModel
from copilot.simgen.signals import AssetSeries

RECOVERY_MINUTES = 30

# (series, model, i0, ramp_n, hold_n, tag) -> None, mutates series in place
FaultFn = Callable[[AssetSeries, AssetModel, int, int, int, str | None], None]


def activation(
    n: int, i0: int, ramp_n: int, hold_n: int, recovery_n: int = RECOVERY_MINUTES
) -> np.ndarray:
    """0 before i0, linear rise to 1 over ramp_n, 1 for hold_n, linear fall over recovery_n."""
    idx = np.arange(n)
    r = np.zeros(n)
    i_peak = i0 + ramp_n
    i_end = i_peak + hold_n
    rising = (idx >= i0) & (idx < i_peak)
    r[rising] = (idx[rising] - i0 + 1) / max(ramp_n, 1)
    r[(idx >= i_peak) & (idx < i_end)] = 1.0
    falling = (idx >= i_end) & (idx < i_end + recovery_n)
    r[falling] = 1.0 - (idx[falling] - i_end + 1) / recovery_n
    return r


def _r(s: AssetSeries, i0: int, ramp_n: int, hold_n: int) -> np.ndarray:
    return activation(len(next(iter(s.values.values()))), i0, ramp_n, hold_n)


def _cooling_restriction(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["TT-102"] += 14.0 * r
    s.values["TT-101"] += 30.0 * r


def _bearing_wear(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["VT-101"] += 6.5 * r
    s.values["TT-101"] += 26.0 * r
    s.values["IT-101"] += 3.0 * r


def _sensor_drift(s, model, i0, ramp_n, hold_n, tag):
    s.values["TT-101"] += 26.0 * _r(s, i0, ramp_n, hold_n)


def _dry_run(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    r_temp = _r(s, i0, 40, ramp_n + hold_n - 40)  # bearing heats more slowly than flow collapses
    s.values["FT-101"] *= 1.0 - 0.95 * r
    s.values["PT-101"] *= 1.0 - 0.8 * r
    s.values["IT-101"] *= 1.0 - 0.35 * r
    s.values["TT-101"] += 28.0 * r_temp


def _cavitation(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["FT-101"] *= 1.0 - 0.55 * r
    s.values["PT-101"] -= 2.0 * r
    s.values["VT-101"] += 5.5 * r


def _sensor_failure(s, model, i0, ramp_n, hold_n, tag):
    v = s.values["TT-101"]
    flat_end = i0 + ramp_n
    v[i0:flat_end] = v[i0]
    s.present["TT-101"][flat_end : flat_end + hold_n] = False


def _hx_fouling(s, model, i0, ramp_n, hold_n, tag):
    r = _r(s, i0, ramp_n, hold_n)
    s.values["TT-201"] += 12.0 * r
    s.values["TT-202"] += 5.0 * r


def _cooler_fan_failure(s, model, i0, ramp_n, hold_n, tag):
    s.values["TT-301"] += 22.0 * _r(s, i0, ramp_n, hold_n)


def _nuisance_spike(s, model, i0, ramp_n, hold_n, tag):
    spec = model.sensor(tag)
    level = spec.hi_limit * 1.08 if spec.hi_limit is not None else spec.lo_limit * 0.9
    s.values[tag][i0 : i0 + hold_n] = level


@dataclass(frozen=True)
class FaultProfile:
    cause_code: str
    asset_model: str | None  # None = any model
    ramp: timedelta
    hold: timedelta
    primary_tag: str | None  # None = taken from the incident
    fn: FaultFn


_H = timedelta(hours=1)
_M = timedelta(minutes=1)

PROFILES: dict[str, FaultProfile] = {p.cause_code: p for p in (
    FaultProfile("COOLING_RESTRICTION", "CP-200", 4 * _H, 3 * _H, "TT-101", _cooling_restriction),
    FaultProfile("BEARING_WEAR", "CP-200", 72 * _H, 6 * _H, "VT-101", _bearing_wear),
    FaultProfile("SENSOR_DRIFT", "CP-200", 48 * _H, 4 * _H, "TT-101", _sensor_drift),
    FaultProfile("DRY_RUN", "CP-200", 10 * _M, 90 * _M, "FT-101", _dry_run),
    FaultProfile("CAVITATION", "CP-200", 2 * _H, 6 * _H, "VT-101", _cavitation),
    FaultProfile("SENSOR_FAILURE", "CP-200", 3 * _H, 1 * _H, "TT-101", _sensor_failure),
    FaultProfile("HX_FOULING", "CL-50", 48 * _H, 6 * _H, "TT-201", _hx_fouling),
    FaultProfile("COOLER_FAN_FAILURE", "AC-75", 3 * _H, 4 * _H, "TT-301", _cooler_fan_failure),
    FaultProfile("NUISANCE_SPIKE", None, timedelta(0), 2 * _M, None, _nuisance_spike),
)}


def _minutes(td: timedelta) -> int:
    return int(td.total_seconds() // 60)


def apply_fault(
    series: AssetSeries, model: AssetModel, profile: FaultProfile, i0: int, tag: str | None = None
) -> None:
    if profile.asset_model is not None and profile.asset_model != model.code:
        raise ValueError(f"{profile.cause_code} applies to {profile.asset_model}, not {model.code}")
    if profile.primary_tag is None and tag is None:
        raise ValueError(f"{profile.cause_code} needs a tag")
    profile.fn(series, model, i0, _minutes(profile.ramp), _minutes(profile.hold), tag)
```

`services/ai-svc/src/copilot/simgen/scenarios.py`:
```python
"""Incident schedule. Start times are local to the tenant, relative to the end of the data."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
from zoneinfo import ZoneInfo


@dataclass(frozen=True)
class Incident:
    incident_id: str
    tenant_id: str
    asset_id: str
    cause_code: str
    days_ago: int  # calendar days before the end date, in tenant local time
    local_start: str  # "HH:MM" tenant local time
    tag: str | None = None  # only for NUISANCE_SPIKE


INCIDENTS: tuple[Incident, ...] = (
    Incident("INC-01", "acme", "P-12", "COOLING_RESTRICTION", 1, "06:00"),  # demo case
    Incident("INC-02", "acme", "P-10", "BEARING_WEAR", 12, "08:00"),
    Incident("INC-03", "acme", "P-11", "SENSOR_DRIFT", 6, "00:00"),
    Incident("INC-04", "acme", "P-12", "DRY_RUN", 9, "14:00"),
    Incident("INC-05", "acme", "CL-1", "HX_FOULING", 15, "00:00"),
    Incident("INC-06", "acme", "AC-1", "COOLER_FAN_FAILURE", 3, "11:00"),
    Incident("INC-07", "acme", "P-11", "SENSOR_FAILURE", 2, "13:00"),
    Incident("INC-08", "acme", "P-10", "NUISANCE_SPIKE", 4, "16:20", tag="TT-101"),
    Incident("INC-11", "globex", "P-20", "CAVITATION", 1, "10:00"),
    Incident("INC-12", "globex", "P-21", "COOLING_RESTRICTION", 5, "07:00"),
    Incident("INC-13", "globex", "CL-2", "HX_FOULING", 8, "00:00"),
    Incident("INC-14", "globex", "AC-2", "COOLER_FAN_FAILURE", 2, "15:00"),
    Incident("INC-15", "globex", "AC-3", "NUISANCE_SPIKE", 7, "03:10", tag="PT-301"),
    Incident("INC-16", "globex", "P-20", "SENSOR_DRIFT", 14, "00:00"),
    Incident("INC-17", "globex", "P-21", "DRY_RUN", 11, "09:00"),
)


def resolve_start(incident: Incident, timezone: str, end: datetime) -> datetime:
    zone = ZoneInfo(timezone)
    day = end.astimezone(zone).date() - timedelta(days=incident.days_ago)
    hh, mm = (int(part) for part in incident.local_start.split(":"))
    return datetime(day.year, day.month, day.day, hh, mm, tzinfo=zone).astimezone(UTC)
```

- [ ] **Step 4: Run test to verify it passes**

Run: `uv run pytest tests/simgen/test_faults.py -v`
Expected: 10 passed

- [ ] **Step 5: Commit**

```bash
git add services/ai-svc/src/copilot/simgen services/ai-svc/tests/simgen
git commit -m "feat(simgen): fault profiles and incident schedule"
```

---

### Task 6: Alarm detection and the generate pipeline with ground truth

**Files:**
- Create: `services/ai-svc/src/copilot/simgen/alarms.py`, `services/ai-svc/src/copilot/simgen/generate.py`
- Test: `services/ai-svc/tests/simgen/test_alarms.py`, `services/ai-svc/tests/simgen/test_generate.py`

**Interfaces:**
- Consumes: Tasks 4–5.
- Produces: `Alarm` (frozen dataclass: `tenant_id, asset_id, tag, alarm_type, severity, raised_at, cleared_at, value, limit_value, message`), `limit_alarms(tenant_id, asset_id, spec, values, present, start) -> list[Alarm]`, `quality_alarms(tenant_id, asset_id, spec, present, quality, start, min_minutes=10) -> list[Alarm]`; `GroundTruth` (with `to_json() -> dict`), `SimResult(start, end, tenants, assets, series, alarms, ground_truth)`, `generate(end: datetime, days: int = 30, seed: int = 7) -> SimResult`.

- [ ] **Step 1: Write the failing tests**

`services/ai-svc/tests/simgen/test_alarms.py`:
```python
from datetime import UTC, datetime, timedelta

import numpy as np

from copilot.simgen.alarms import limit_alarms, quality_alarms
from copilot.simgen.catalog import MODELS
from copilot.simgen.signals import BAD, GOOD

START = datetime(2026, 9, 1, tzinfo=UTC)
TT101 = MODELS["CP-200"].sensor("TT-101")  # hi 85
FT101 = MODELS["CP-200"].sensor("FT-101")  # lo 60


def _present(n):
    return np.ones(n, dtype=bool)


def test_high_alarm_raised_and_cleared_with_deadband():
    v = np.array([80, 86, 84, 84, 83.2, 80], dtype=float)  # 84 is inside the 1.7 deadband
    [a] = limit_alarms("acme", "P-12", TT101, v, _present(6), START)
    assert a.alarm_type == "high_temp"
    assert a.raised_at == START + timedelta(minutes=1)
    assert a.cleared_at == START + timedelta(minutes=4)
    assert a.value == 86.0 and a.limit_value == 85.0
    assert a.severity == "medium"
    assert "Pump bearing temperature" in a.message


def test_severity_high_when_exceeding_limit_by_over_ten_percent():
    v = np.array([80, 86, 95, 80], dtype=float)
    [a] = limit_alarms("acme", "P-12", TT101, v, _present(4), START)
    assert a.severity == "high"


def test_low_alarm_and_unclosed_alarm():
    v = np.array([120, 70, 50, 40], dtype=float)
    [a] = limit_alarms("acme", "P-12", FT101, v, _present(4), START)
    assert a.alarm_type == "low_flow"
    assert a.cleared_at is None


def test_missing_samples_do_not_raise_or_clear():
    v = np.array([80, 90, 0, 0, 90], dtype=float)
    present = np.array([True, True, False, False, True])
    [a] = limit_alarms("acme", "P-12", TT101, v, present, START)
    assert a.cleared_at is None


def test_no_alarm_when_within_limits():
    assert limit_alarms("acme", "P-12", TT101, np.full(100, 70.0), _present(100), START) == []


def test_bad_quality_alarm_needs_ten_minutes():
    present = np.ones(60, dtype=bool)
    present[5:12] = False  # 7 min: ignored
    present[30:45] = False  # 15 min: alarm
    quality = np.full(60, GOOD, dtype=np.int16)
    [a] = quality_alarms("acme", "P-11", TT101, present, quality, START)
    assert a.alarm_type == "bad_quality"
    assert a.raised_at == START + timedelta(minutes=30)
    assert a.cleared_at == START + timedelta(minutes=45)
    assert a.value is None and a.limit_value is None


def test_bad_quality_code_counts_as_bad():
    quality = np.full(30, BAD, dtype=np.int16)
    [a] = quality_alarms("acme", "P-11", TT101, np.ones(30, dtype=bool), quality, START)
    assert a.cleared_at is None
```

`services/ai-svc/tests/simgen/test_generate.py`:
```python
from datetime import UTC, datetime

import pytest

from copilot.simgen.generate import generate

END = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)


@pytest.fixture(scope="module")
def result():
    return generate(END, days=30, seed=7)


def test_window_and_shapes(result):
    assert result.end == END
    assert (result.end - result.start).days == 30
    for series in result.series.values():
        for values in series.values.values():
            assert len(values) == 30 * 1440


def test_every_incident_raises_an_alarm_on_its_primary_tag(result):
    for gt in result.ground_truth:
        tags = {a["tag"] for a in gt.alarms}
        assert gt.primary_tag in tags, gt.incident_id


def test_every_alarm_belongs_to_exactly_one_incident(result):
    for alarm in result.alarms:
        owners = [
            gt.incident_id
            for gt in result.ground_truth
            if any(
                a["tag"] == alarm.tag and a["raised_at"] == alarm.raised_at.isoformat()
                for a in gt.alarms
            )
            and gt.tenant_id == alarm.tenant_id
            and gt.asset_id == alarm.asset_id
        ]
        assert len(owners) == 1, alarm


def test_sensor_drift_alarms_only_on_the_drifting_tag(result):
    for gt in result.ground_truth:
        if gt.cause_code == "SENSOR_DRIFT":
            assert {a["tag"] for a in gt.alarms} == {"TT-101"}


def test_demo_incident_alarm_is_yesterday_for_acme(result):
    gt = next(g for g in result.ground_truth if g.incident_id == "INC-01")
    assert (gt.tenant_id, gt.asset_id, gt.cause_code) == ("acme", "P-12", "COOLING_RESTRICTION")
    assert any(a["alarm_type"] == "high_temp" and a["tag"] == "TT-101" for a in gt.alarms)


def test_deterministic_for_same_inputs(result):
    again = generate(END, days=30, seed=7)
    assert again.alarms == result.alarms


def test_ground_truth_json_is_serialisable(result):
    row = result.ground_truth[0].to_json()
    assert row["synthetic"] is True
    assert isinstance(row["start"], str)


def test_naive_end_rejected():
    with pytest.raises(ValueError, match="timezone"):
        generate(datetime(2026, 10, 17), days=30)


def test_too_few_days_rejected():
    with pytest.raises(ValueError, match="outside the simulated window"):
        generate(END, days=5)
```

- [ ] **Step 2: Run tests to verify they fail**

Run: `uv run pytest tests/simgen/test_alarms.py tests/simgen/test_generate.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copilot.simgen.alarms'`

- [ ] **Step 3: Implement alarm detection**

`services/ai-svc/src/copilot/simgen/alarms.py`:
```python
"""Alarm detection over generated series: limit alarms with deadband, and bad-quality alarms."""

from dataclasses import dataclass
from datetime import datetime, timedelta

import numpy as np

from copilot.simgen.catalog import SensorSpec
from copilot.simgen.signals import BAD

DEADBAND = 0.02  # fraction of the limit
BAD_QUALITY_MIN_MINUTES = 10
KIND_SHORT = {
    "temperature": "temp",
    "flow": "flow",
    "pressure": "pressure",
    "current": "current",
    "vibration": "vibration",
}


@dataclass(frozen=True)
class Alarm:
    tenant_id: str
    asset_id: str
    tag: str
    alarm_type: str
    severity: str
    raised_at: datetime
    cleared_at: datetime | None
    value: float | None
    limit_value: float | None
    message: str


def _runs(trigger: np.ndarray, clear: np.ndarray) -> list[tuple[int, int | None]]:
    runs: list[tuple[int, int | None]] = []
    active, begin = False, 0
    for i in range(len(trigger)):
        if not active and trigger[i]:
            active, begin = True, i
        elif active and clear[i]:
            runs.append((begin, i))
            active = False
    if active:
        runs.append((begin, None))
    return runs


def limit_alarms(
    tenant_id: str,
    asset_id: str,
    spec: SensorSpec,
    values: np.ndarray,
    present: np.ndarray,
    start: datetime,
) -> list[Alarm]:
    v = np.where(present, values, np.nan)
    out: list[Alarm] = []
    for direction, limit in (("high", spec.hi_limit), ("low", spec.lo_limit)):
        if limit is None:
            continue
        band = DEADBAND * abs(limit)
        if direction == "high":
            trigger, clear = v > limit, v < limit - band
        else:
            trigger, clear = v < limit, v > limit + band
        if not trigger.any():
            continue
        for i0, i1 in _runs(trigger, clear):
            segment = v[i0:i1] if i1 is not None else v[i0:]
            worst = np.nanmax(segment) if direction == "high" else np.nanmin(segment)
            value = round(float(v[i0]), 2)
            symbol = ">" if direction == "high" else "<"
            out.append(Alarm(
                tenant_id=tenant_id,
                asset_id=asset_id,
                tag=spec.tag,
                alarm_type=f"{direction}_{KIND_SHORT[spec.kind]}",
                severity="high" if abs(worst - limit) / abs(limit) > 0.10 else "medium",
                raised_at=start + timedelta(minutes=i0),
                cleared_at=None if i1 is None else start + timedelta(minutes=i1),
                value=value,
                limit_value=limit,
                message=(
                    f"{spec.description} {direction}: {value:.1f} {spec.unit} "
                    f"{symbol} limit {limit:g} {spec.unit}"
                ),
            ))
    return out


def quality_alarms(
    tenant_id: str,
    asset_id: str,
    spec: SensorSpec,
    present: np.ndarray,
    quality: np.ndarray,
    start: datetime,
    min_minutes: int = BAD_QUALITY_MIN_MINUTES,
) -> list[Alarm]:
    bad = ~present | (quality == BAD)
    if not bad.any():
        return []
    out: list[Alarm] = []
    for i0, i1 in _runs(bad, ~bad):
        length = (len(bad) if i1 is None else i1) - i0
        if length < min_minutes:
            continue
        out.append(Alarm(
            tenant_id=tenant_id,
            asset_id=asset_id,
            tag=spec.tag,
            alarm_type="bad_quality",
            severity="medium",
            raised_at=start + timedelta(minutes=i0),
            cleared_at=None if i1 is None else start + timedelta(minutes=i1),
            value=None,
            limit_value=None,
            message=f"{spec.description}: bad or missing signal for {length} min",
        ))
    return out
```

- [ ] **Step 4: Implement generate**

`services/ai-svc/src/copilot/simgen/generate.py`:
```python
"""Generate a full synthetic dataset: series per asset, alarms and ground truth per incident."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import numpy as np

from copilot.simgen.alarms import Alarm, limit_alarms, quality_alarms
from copilot.simgen.catalog import ASSETS, MODELS, TENANTS, Asset, Tenant
from copilot.simgen.faults import PROFILES, RECOVERY_MINUTES, apply_fault
from copilot.simgen.scenarios import INCIDENTS, resolve_start
from copilot.simgen.signals import AssetSeries, normal_series


@dataclass
class GroundTruth:
    incident_id: str
    tenant_id: str
    asset_id: str
    cause_code: str
    primary_tag: str
    start: datetime
    peak: datetime
    end: datetime
    alarms: list[dict]  # {"tag", "alarm_type", "raised_at"} with ISO timestamps

    def to_json(self) -> dict:
        return {
            "incident_id": self.incident_id,
            "tenant_id": self.tenant_id,
            "asset_id": self.asset_id,
            "cause_code": self.cause_code,
            "primary_tag": self.primary_tag,
            "start": self.start.isoformat(),
            "peak": self.peak.isoformat(),
            "end": self.end.isoformat(),
            "alarms": self.alarms,
            "synthetic": True,
        }


@dataclass
class SimResult:
    start: datetime
    end: datetime
    tenants: tuple[Tenant, ...]
    assets: tuple[Asset, ...]
    series: dict[tuple[str, str], AssetSeries]
    alarms: list[Alarm]
    ground_truth: list[GroundTruth]


def generate(end: datetime, days: int = 30, seed: int = 7) -> SimResult:
    if end.tzinfo is None:
        raise ValueError("end must be timezone-aware")
    end = end.astimezone(UTC).replace(second=0, microsecond=0)
    start = end - timedelta(days=days)
    n = days * 1440
    rng = np.random.default_rng(seed)
    timezones = {t.id: t.timezone for t in TENANTS}
    assets = {(a.tenant_id, a.id): a for a in ASSETS}
    series = {key: normal_series(MODELS[a.model], start, n, rng) for key, a in assets.items()}
    recovery = timedelta(minutes=RECOVERY_MINUTES)

    windows = []
    for inc in INCIDENTS:
        profile = PROFILES[inc.cause_code]
        asset = assets[(inc.tenant_id, inc.asset_id)]
        t0 = resolve_start(inc, timezones[inc.tenant_id], end)
        t_end = t0 + profile.ramp + profile.hold
        if t0 < start or t_end + recovery > end:
            raise ValueError(f"{inc.incident_id} falls outside the simulated window; increase days")
        i0 = int((t0 - start).total_seconds() // 60)
        apply_fault(series[(asset.tenant_id, asset.id)], MODELS[asset.model], profile, i0, inc.tag)
        windows.append((inc, profile, t0, t_end))

    alarms: list[Alarm] = []
    for (tenant_id, asset_id), asset in assets.items():
        s = series[(tenant_id, asset_id)]
        for spec in MODELS[asset.model].sensors:
            tag = spec.tag
            alarms += limit_alarms(tenant_id, asset_id, spec, s.values[tag], s.present[tag], start)
            alarms += quality_alarms(
                tenant_id, asset_id, spec, s.present[tag], s.quality[tag], start
            )
    alarms.sort(key=lambda a: (a.tenant_id, a.raised_at, a.asset_id, a.tag))

    ground_truth = []
    for inc, profile, t0, t_end in windows:
        linked = [
            {"tag": a.tag, "alarm_type": a.alarm_type, "raised_at": a.raised_at.isoformat()}
            for a in alarms
            if a.tenant_id == inc.tenant_id
            and a.asset_id == inc.asset_id
            and t0 <= a.raised_at <= t_end + recovery
        ]
        ground_truth.append(GroundTruth(
            incident_id=inc.incident_id,
            tenant_id=inc.tenant_id,
            asset_id=inc.asset_id,
            cause_code=inc.cause_code,
            primary_tag=profile.primary_tag or inc.tag,
            start=t0,
            peak=t0 + profile.ramp,
            end=t_end,
            alarms=linked,
        ))

    return SimResult(start, end, TENANTS, ASSETS, series, alarms, ground_truth)
```

- [ ] **Step 5: Run tests to verify they pass**

Run: `uv run pytest tests/simgen -v`
Expected: all simgen tests pass (5 + 10 + 7 + 9). If `test_every_alarm_belongs_to_exactly_one_incident` fails, a baseline or fault crosses a limit unexpectedly — adjust the fault magnitude in `faults.py`, not the test.

- [ ] **Step 6: Commit**

```bash
git add services/ai-svc/src/copilot/simgen services/ai-svc/tests/simgen
git commit -m "feat(simgen): alarm detection and ground-truth generation"
```

---

### Task 7: Loader and `copilot simgen` command

**Files:**
- Create: `services/ai-svc/src/copilot/simgen/loader.py`
- Modify: `services/ai-svc/src/copilot/cli.py` (add `simgen` command)
- Test: `services/ai-svc/tests/simgen/test_loader.py` (ground-truth file only; DB load verified by the CLI run)

**Interfaces:**
- Consumes: `SimResult`, `generate` (Task 6), `Settings.dsn` (Task 1).
- Produces: `load(result: SimResult, settings: Settings) -> None`, `tenant_counts(settings: Settings, tenant_ids: list[str]) -> dict[str, dict[str, int]]` (keys: `assets`, `sensors`, `alarms`, `sensor_readings`, `foreign`), `write_ground_truth(result: SimResult, path: Path) -> None`; CLI `copilot simgen [--days 30] [--seed 7] [--end ISO] [--out PATH]`.

- [ ] **Step 1: Write the failing test**

`services/ai-svc/tests/simgen/test_loader.py`:
```python
import json
from datetime import UTC, datetime

from copilot.simgen.generate import generate
from copilot.simgen.loader import write_ground_truth


def test_write_ground_truth_jsonl(tmp_path):
    result = generate(datetime(2026, 10, 17, 8, 0, tzinfo=UTC))
    path = tmp_path / "nested" / "gt.jsonl"
    write_ground_truth(result, path)
    rows = [json.loads(line) for line in path.read_text(encoding="utf-8").splitlines()]
    assert len(rows) == len(result.ground_truth)
    assert {r["incident_id"] for r in rows} >= {"INC-01", "INC-11"}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `uv run pytest tests/simgen/test_loader.py -v`
Expected: FAIL — `ModuleNotFoundError: No module named 'copilot.simgen.loader'`

- [ ] **Step 3: Implement the loader**

`services/ai-svc/src/copilot/simgen/loader.py`:
```python
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
```

- [ ] **Step 4: Add the CLI command**

Replace `services/ai-svc/src/copilot/cli.py` with:
```python
"""`copilot` command-line entry point."""

from datetime import UTC, datetime
from pathlib import Path

import psycopg
import typer

from copilot.config import REPO_ROOT, get_settings
from copilot.db.migrate import apply_migrations, set_role_passwords

app = typer.Typer(no_args_is_help=True, add_completion=False)


@app.command()
def migrate() -> None:
    """Apply pending database migrations and set service role passwords."""
    settings = get_settings()
    with psycopg.connect(settings.dsn("app_owner"), autocommit=True) as conn:
        applied = apply_migrations(conn)
        set_role_passwords(conn, settings)
    typer.echo(f"applied: {', '.join(applied) if applied else 'nothing (up to date)'}")


@app.command()
def simgen(
    days: int = typer.Option(30, help="Days of 1-minute data to generate."),
    seed: int = typer.Option(7, help="Random seed."),
    end: str | None = typer.Option(None, help="ISO-8601 end time with offset; default now (UTC)."),
    out: Path = typer.Option(REPO_ROOT / "data" / "ground_truth.jsonl", help="Ground-truth file."),
) -> None:
    """Generate synthetic telemetry/alarms, load them per tenant and write ground truth."""
    from copilot.simgen.generate import generate
    from copilot.simgen.loader import load, tenant_counts, write_ground_truth

    end_dt = datetime.fromisoformat(end) if end else datetime.now(UTC)
    if end_dt.tzinfo is None:
        raise typer.BadParameter("--end must include a timezone offset, e.g. 2026-10-17T08:00+00:00")
    settings = get_settings()
    result = generate(end_dt, days=days, seed=seed)
    typer.echo(f"generated {result.start.isoformat()} .. {result.end.isoformat()} "
               f"({len(result.alarms)} alarms, {len(result.ground_truth)} incidents)")
    load(result, settings)
    write_ground_truth(result, out)
    counts = tenant_counts(settings, [t.id for t in result.tenants])
    for tenant_id, c in counts.items():
        typer.echo(f"{tenant_id}: " + ", ".join(f"{k}={v}" for k, v in c.items()))
    typer.echo(f"ground truth: {out}")
    if any(c["foreign"] for c in counts.values()):
        typer.echo("ERROR: rows from another tenant were visible", err=True)
        raise typer.Exit(1)
```

- [ ] **Step 5: Run unit tests, then load real data**

Run: `uv run pytest -v`
Expected: all unit tests pass.

Run: `uv run copilot simgen`
Expected (counts approximate): `acme: foreign=0, assets=5, sensors=26, alarms=…, sensor_readings≈1123000` and `globex: foreign=0, assets=5, sensors=24, sensor_readings≈1036800 …`, ground truth written to `data/ground_truth.jsonl`.

Run it a second time; expected: same counts (reload is idempotent).

- [ ] **Step 6: Commit**

```bash
git add services/ai-svc/src/copilot/simgen/loader.py services/ai-svc/src/copilot/cli.py services/ai-svc/tests/simgen/test_loader.py
git commit -m "feat(simgen): per-tenant loader, ground-truth file and simgen command"
```

---

### Task 8: Go data-svc skeleton

**Files:**
- Create: `services/data-svc/go.mod`, `services/data-svc/cmd/data-svc/main.go`, `services/data-svc/internal/api/router.go`
- Test: `services/data-svc/internal/api/router_test.go`

**Interfaces:**
- Produces: `api.NewRouter() http.Handler` with `GET /healthz` → `200 {"status":"ok"}`. M1 adds routes here.

- [ ] **Step 1: Write the failing test**

`services/data-svc/go.mod`:
```
module indagent/data-svc

go 1.25
```

`services/data-svc/internal/api/router_test.go`:
```go
package api

import (
	"net/http"
	"net/http/httptest"
	"strings"
	"testing"
)

func TestHealthz(t *testing.T) {
	tests := []struct {
		name       string
		method     string
		wantStatus int
		wantBody   string
	}{
		{"get returns ok", http.MethodGet, http.StatusOK, `{"status":"ok"}`},
		{"post not allowed", http.MethodPost, http.StatusMethodNotAllowed, ""},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			rec := httptest.NewRecorder()
			NewRouter().ServeHTTP(rec, httptest.NewRequest(tt.method, "/healthz", nil))
			if rec.Code != tt.wantStatus {
				t.Fatalf("status = %d, want %d", rec.Code, tt.wantStatus)
			}
			if tt.wantBody != "" && strings.TrimSpace(rec.Body.String()) != tt.wantBody {
				t.Fatalf("body = %q, want %q", rec.Body.String(), tt.wantBody)
			}
		})
	}
}
```

- [ ] **Step 2: Run test to verify it fails**

Run: `cd services/data-svc && go test ./...`
Expected: FAIL — `undefined: NewRouter`

- [ ] **Step 3: Implement router and main**

`services/data-svc/internal/api/router.go`:
```go
// Package api holds the HTTP routes served by data-svc.
package api

import (
	"encoding/json"
	"net/http"
)

// NewRouter returns the data-svc HTTP handler.
func NewRouter() http.Handler {
	mux := http.NewServeMux()
	mux.HandleFunc("GET /healthz", health)
	return mux
}

func health(w http.ResponseWriter, _ *http.Request) {
	w.Header().Set("Content-Type", "application/json")
	_ = json.NewEncoder(w).Encode(map[string]string{"status": "ok"})
}
```

`services/data-svc/cmd/data-svc/main.go`:
```go
// Command data-svc serves read-only, tenant-scoped access to alarms and telemetry.
package main

import (
	"log/slog"
	"net/http"
	"os"
	"time"

	"indagent/data-svc/internal/api"
)

func main() {
	logger := slog.New(slog.NewJSONHandler(os.Stdout, nil))
	addr := os.Getenv("DATASVC_ADDR")
	if addr == "" {
		addr = ":8081"
	}
	srv := &http.Server{
		Addr:              addr,
		Handler:           api.NewRouter(),
		ReadHeaderTimeout: 5 * time.Second,
		ReadTimeout:       10 * time.Second,
		WriteTimeout:      30 * time.Second,
	}
	logger.Info("data-svc listening", "addr", addr)
	if err := srv.ListenAndServe(); err != nil {
		logger.Error("server stopped", "err", err)
		os.Exit(1)
	}
}
```

- [ ] **Step 4: Run tests and vet**

Run: `go test ./... && go vet ./...`
Expected: `ok  indagent/data-svc/internal/api`, no vet output.

- [ ] **Step 5: Commit**

```bash
git add services/data-svc
git commit -m "feat(data-svc): Go module skeleton with healthz"
```

---

### Task 9: M0 exit check and docs

**Files:**
- Modify: `docs/plan.md` (mark M0 done)

- [ ] **Step 1: Run the full M0 exit criteria**

```bash
docker compose up -d --wait
cd services/ai-svc
uv run copilot migrate            # applied: nothing (up to date)
uv run pytest -v                  # unit tests pass
uv run pytest -m integration -v   # 8 passed
uv run copilot simgen             # foreign=0 for both tenants
cd ../data-svc && go test ./...
```
Expected: every command succeeds; `foreign=0` for `acme` and `globex`.

- [ ] **Step 2: Mark M0 done in `docs/plan.md`**

Change the heading `## M0 — Foundations` to `## M0 — Foundations ✅ (2026-10-17)`.

- [ ] **Step 3: Commit**

```bash
git add docs/plan.md
git commit -m "docs: mark M0 complete"
```
