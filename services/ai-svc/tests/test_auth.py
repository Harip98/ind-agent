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


from copilot.auth import Principal, TokenError, verify_token  # noqa: E402


def _tok(**override):
    now = datetime.now(UTC).replace(microsecond=0)
    claims = {"iss": ISSUER, "aud": AUDIENCE, "sub": "demo", "tenant_id": "acme", "roles": ["engineer"],
              "iat": int((now - timedelta(minutes=1)).timestamp()),
              "exp": int((now + timedelta(minutes=30)).timestamp())}
    claims.update(override)
    claims = {k: v for k, v in claims.items() if v is not None}
    return jwt.encode(claims, SECRET, algorithm="HS256")


def test_verify_token_returns_principal():
    tok = issue_token("acme", "demo", SECRET)
    assert verify_token(tok, SECRET) == Principal("acme", "demo", ("engineer",), tok)


@pytest.mark.parametrize("token", [
    "garbage",
    jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "d", "tenant_id": "acme"}, SECRET, algorithm="HS256"),
    jwt.encode({"iss": ISSUER, "aud": AUDIENCE, "sub": "d", "tenant_id": "acme", "iat": 1, "exp": 4102444800},
               "y" * 32, algorithm="HS256"),
])
def test_verify_token_rejects_malformed(token):
    with pytest.raises(TokenError):
        verify_token(token, SECRET)


@pytest.mark.parametrize("override", [
    {"exp": int((datetime.now(UTC) - timedelta(minutes=5)).timestamp())},
    {"aud": "other"}, {"iss": "evil"}, {"tenant_id": ""}, {"tenant_id": None}, {"sub": None},
    {"iat": None}, {"exp": int((datetime.now(UTC) + timedelta(hours=2)).timestamp())},
    {"iat": int((datetime.now(UTC) + timedelta(minutes=10)).timestamp())},
])
def test_verify_token_rejects_bad_claims(override):
    with pytest.raises(TokenError):
        verify_token(_tok(**override), SECRET)


def test_verify_token_rejects_other_algorithms():
    with pytest.raises(TokenError):
        verify_token(jwt.encode({"sub": "d"}, SECRET, algorithm="HS512"), SECRET)
