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
    assert s.dsn("ops_reader") == (
        "host=db port=1234 dbname=x user=ops_reader password=secret connect_timeout=5"
    )


def test_default_host_is_ipv4_loopback():
    """Docker binds 127.0.0.1 only; 'localhost' tries ::1 first on Windows and can hang."""
    assert Settings.model_fields["pg_host"].default == "127.0.0.1"


def test_owner_password_maps_to_app_owner():
    s = Settings(owner_password="own")
    assert s.password_for("app_owner") == "own"


def test_unknown_role_rejected():
    with pytest.raises(ValueError):
        Settings().dsn("postgres")


def test_roles_are_the_five_documented_roles():
    assert ROLES == ("app_owner", "ops_reader", "ops_loader", "kb_reader", "kb_writer")
