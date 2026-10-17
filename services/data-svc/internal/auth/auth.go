// Package auth verifies the short-lived HS256 tokens that identify a user and their tenant.
package auth

import (
	"errors"
	"fmt"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

const (
	Issuer      = "ind-agent-dev"
	Audience    = "ind-agent"
	MaxLifetime = time.Hour
	leeway      = 30 * time.Second
)

// ErrUnauthorized wraps every verification failure.
var ErrUnauthorized = errors.New("unauthorized")

// Claims are the token claims the services rely on.
type Claims struct {
	TenantID string   `json:"tenant_id"`
	Roles    []string `json:"roles"`
	jwt.RegisteredClaims
}

// Verifier checks token signature, issuer, audience, lifetime and required claims.
type Verifier struct {
	secret []byte
	now    func() time.Time
}

// NewVerifier requires a secret of at least 32 bytes.
func NewVerifier(secret []byte, now func() time.Time) (*Verifier, error) {
	if len(secret) < 32 {
		return nil, errors.New("jwt secret must be at least 32 bytes")
	}
	return &Verifier{secret: secret, now: now}, nil
}

// Verify parses and validates a token, returning its claims.
func (v *Verifier) Verify(token string) (*Claims, error) {
	claims := &Claims{}
	_, err := jwt.ParseWithClaims(token, claims,
		func(*jwt.Token) (any, error) { return v.secret, nil },
		jwt.WithValidMethods([]string{jwt.SigningMethodHS256.Alg()}),
		jwt.WithIssuer(Issuer),
		jwt.WithAudience(Audience),
		jwt.WithExpirationRequired(),
		jwt.WithIssuedAt(),
		jwt.WithLeeway(leeway),
		jwt.WithTimeFunc(v.now),
	)
	if err != nil {
		return nil, fmt.Errorf("%w: %v", ErrUnauthorized, err)
	}
	switch {
	case claims.TenantID == "" || claims.Subject == "":
		return nil, fmt.Errorf("%w: tenant_id and sub are required", ErrUnauthorized)
	case claims.IssuedAt == nil:
		return nil, fmt.Errorf("%w: iat is required", ErrUnauthorized)
	case claims.ExpiresAt.Sub(claims.IssuedAt.Time) > MaxLifetime:
		return nil, fmt.Errorf("%w: token lifetime exceeds %s", ErrUnauthorized, MaxLifetime)
	}
	return claims, nil
}
