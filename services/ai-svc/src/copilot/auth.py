"""Development token issuer. A real IdP (OIDC) replaces this; services only verify tokens."""

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
