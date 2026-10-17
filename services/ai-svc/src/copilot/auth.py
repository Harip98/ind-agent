"""Development token issuer. A real IdP (OIDC) replaces this; services only verify tokens."""

from dataclasses import dataclass
from datetime import UTC, datetime, timedelta

import jwt

ISSUER = "ind-agent-dev"
AUDIENCE = "ind-agent"
MAX_TTL = timedelta(hours=1)


def issue_token(
    tenant_id: str,
    sub: str,
    secret: str,
    ttl: timedelta = timedelta(minutes=60),
    roles: tuple[str, ...] = ("engineer",),
    now: datetime | None = None,
) -> str:
    if len(secret.encode()) < 32:
        raise ValueError("JWT secret must be at least 32 bytes")
    if ttl > MAX_TTL:
        raise ValueError("token lifetime must be at most 1 hour")
    if not tenant_id.strip() or not sub.strip():
        raise ValueError("tenant and subject are required")
    issued = (now or datetime.now(UTC)).replace(microsecond=0)
    claims = {
        "iss": ISSUER,
        "aud": AUDIENCE,
        "sub": sub,
        "tenant_id": tenant_id,
        "roles": list(roles),
        "iat": int(issued.timestamp()),
        "exp": int((issued + ttl).timestamp()),
    }
    return jwt.encode(claims, secret, algorithm="HS256")


class TokenError(ValueError):
    """Missing, malformed, expired or otherwise unacceptable token."""


@dataclass(frozen=True)
class Principal:
    tenant_id: str
    sub: str
    roles: tuple[str, ...]
    token: str


def verify_token(token: str, secret: str) -> Principal:
    """Same rules as data-svc: HS256, iss/aud, exp+iat required, lifetime <= 1 h, 30 s leeway."""
    try:
        claims = jwt.decode(
            token, secret, algorithms=["HS256"], audience=AUDIENCE, issuer=ISSUER, leeway=30,
            options={"require": ["exp", "iat", "sub", "iss", "aud"]},
        )
    except jwt.PyJWTError as exc:
        raise TokenError(str(exc)) from exc
    now = datetime.now(UTC).timestamp()
    tenant, sub = claims.get("tenant_id"), claims.get("sub")
    if not isinstance(tenant, str) or not tenant or not isinstance(sub, str) or not sub:
        raise TokenError("tenant_id and sub are required")
    if claims["iat"] > now + 30:
        raise TokenError("token issued in the future")
    if claims["exp"] - claims["iat"] > MAX_TTL.total_seconds():
        raise TokenError("token lifetime exceeds 1 hour")
    roles = claims.get("roles") or []
    return Principal(tenant, sub, tuple(str(r) for r in roles), token)
