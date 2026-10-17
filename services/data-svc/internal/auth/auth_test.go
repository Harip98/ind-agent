package auth

import (
	"errors"
	"testing"
	"time"

	"github.com/golang-jwt/jwt/v5"
)

var (
	secret = []byte("0123456789abcdef0123456789abcdef")
	now    = time.Date(2026, 10, 17, 8, 0, 0, 0, time.UTC)
)

func sign(t *testing.T, method jwt.SigningMethod, key any, mutate func(jwt.MapClaims)) string {
	t.Helper()
	c := jwt.MapClaims{
		"iss": Issuer, "aud": Audience, "sub": "demo", "tenant_id": "acme",
		"iat": now.Add(-time.Minute).Unix(), "exp": now.Add(30 * time.Minute).Unix(),
	}
	if mutate != nil {
		mutate(c)
	}
	s, err := jwt.NewWithClaims(method, c).SignedString(key)
	if err != nil {
		t.Fatal(err)
	}
	return s
}

func TestVerify(t *testing.T) {
	v, err := NewVerifier(secret, func() time.Time { return now })
	if err != nil {
		t.Fatal(err)
	}
	tests := []struct {
		name    string
		token   string
		wantErr bool
	}{
		{"valid", sign(t, jwt.SigningMethodHS256, secret, nil), false},
		{"expired", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) {
			c["exp"] = now.Add(-2 * time.Minute).Unix()
		}), true},
		{"wrong secret", sign(t, jwt.SigningMethodHS256, []byte("ffffffffffffffffffffffffffffffff"), nil), true},
		{"alg none", sign(t, jwt.SigningMethodNone, jwt.UnsafeAllowNoneSignatureType, nil), true},
		{"hs512 not allowed", sign(t, jwt.SigningMethodHS512, secret, nil), true},
		{"wrong audience", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) { c["aud"] = "other" }), true},
		{"wrong issuer", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) { c["iss"] = "evil" }), true},
		{"missing tenant", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) { delete(c, "tenant_id") }), true},
		{"missing subject", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) { delete(c, "sub") }), true},
		{"missing exp", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) { delete(c, "exp") }), true},
		{"missing iat", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) { delete(c, "iat") }), true},
		{"lifetime over 1h", sign(t, jwt.SigningMethodHS256, secret, func(c jwt.MapClaims) {
			c["exp"] = now.Add(2 * time.Hour).Unix()
		}), true},
		{"garbage", "not-a-jwt", true},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			claims, err := v.Verify(tt.token)
			if tt.wantErr {
				if !errors.Is(err, ErrUnauthorized) {
					t.Fatalf("err = %v, want ErrUnauthorized", err)
				}
				return
			}
			if err != nil {
				t.Fatalf("unexpected error: %v", err)
			}
			if claims.TenantID != "acme" || claims.Subject != "demo" {
				t.Fatalf("claims = %+v", claims)
			}
		})
	}
}

func TestNewVerifierRejectsShortSecret(t *testing.T) {
	if _, err := NewVerifier([]byte("short"), time.Now); err == nil {
		t.Fatal("expected error for short secret")
	}
}
