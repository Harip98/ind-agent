from datetime import UTC, datetime, timedelta

import jwt
import pytest

from copilot.auth import AUDIENCE, ISSUER, issue_token

SECRET = "x" * 32


def test_token_carries_tenant_subject_and_standard_claims():
    now = datetime(2026, 10, 17, 8, 0, tzinfo=UTC)
    token = issue_token("acme", "demo-user", SECRET, now=now)
    claims = jwt.decode(token, SECRET, algorithms=["HS256"], audience=AUDIENCE,
                        options={"verify_exp": False})
    assert claims["tenant_id"] == "acme"
    assert claims["sub"] == "demo-user"
    assert claims["iss"] == ISSUER
    assert claims["roles"] == ["engineer"]
    assert claims["exp"] - claims["iat"] == 3600


def test_ttl_over_one_hour_rejected():
    with pytest.raises(ValueError, match="1 hour"):
        issue_token("acme", "u", SECRET, ttl=timedelta(hours=2))


def test_short_secret_rejected():
    with pytest.raises(ValueError, match="32"):
        issue_token("acme", "u", "short")


def test_blank_tenant_rejected():
    with pytest.raises(ValueError, match="tenant"):
        issue_token(" ", "u", SECRET)
