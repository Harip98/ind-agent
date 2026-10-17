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
    assert versions == ["001_roles_schemas", "002_ops", "003_kb", "004_rls", "005_kb_structural_tenancy", "006_alarm_identity", "007_kb_embed_model", "008_kb_exact_knn", "009_kb_replaced_by"]


def test_checksum_mismatch_detected(tmp_path):
    import hashlib

    from copilot.db.migrate import MigrationChecksumError, check_checksums

    (tmp_path / "001_a.sql").write_bytes(b"select 1;")
    check_checksums(tmp_path, {"001_a": hashlib.sha256(b"select 1;").hexdigest()})
    check_checksums(tmp_path, {"001_a": None})
    with pytest.raises(MigrationChecksumError, match="001_a"):
        check_checksums(tmp_path, {"001_a": "deadbeef"})
