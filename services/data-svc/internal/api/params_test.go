package api

import (
	"net/url"
	"testing"
	"time"
)

func TestParseWindow(t *testing.T) {
	tests := []struct {
		name     string
		from, to string
		max      time.Duration
		wantCode string
	}{
		{"ok", "2026-10-16T00:00:00Z", "2026-10-17T00:00:00Z", 72 * time.Hour, ""},
		{"offset accepted", "2026-10-16T05:30:00+05:30", "2026-10-17T00:00:00Z", 72 * time.Hour, ""},
		{"missing from", "", "2026-10-17T00:00:00Z", 72 * time.Hour, "invalid_range"},
		{"bad timestamp", "yesterday", "2026-10-17T00:00:00Z", 72 * time.Hour, "invalid_range"},
		{"reversed", "2026-10-17T00:00:00Z", "2026-10-16T00:00:00Z", 72 * time.Hour, "invalid_range"},
		{"empty", "2026-10-17T00:00:00Z", "2026-10-17T00:00:00Z", 72 * time.Hour, "invalid_range"},
		{"too large", "2026-10-13T00:00:00Z", "2026-10-17T00:00:01Z", 72 * time.Hour, "window_too_large"},
	}
	for _, tt := range tests {
		t.Run(tt.name, func(t *testing.T) {
			q := url.Values{}
			if tt.from != "" {
				q.Set("from", tt.from)
			}
			q.Set("to", tt.to)
			_, _, err := parseWindow(q, tt.max)
			got := ""
			if err != nil {
				got = err.code
			}
			if got != tt.wantCode {
				t.Fatalf("code = %q, want %q", got, tt.wantCode)
			}
		})
	}
}

func TestBucketSeconds(t *testing.T) {
	base := time.Date(2026, 10, 1, 0, 0, 0, 0, time.UTC)
	tests := []struct {
		span time.Duration
		want int
	}{
		{8 * time.Hour, 60},
		{72 * time.Hour, 130},
		{time.Minute, 60},
	}
	for _, tt := range tests {
		if got := bucketSeconds(base, base.Add(tt.span)); got != tt.want {
			t.Errorf("bucketSeconds(%s) = %d, want %d", tt.span, got, tt.want)
		}
		if pts := int(tt.span.Seconds()) / bucketSeconds(base, base.Add(tt.span)); pts > MaxPoints {
			t.Errorf("%s gives %d points", tt.span, pts)
		}
	}
}

func TestValidID(t *testing.T) {
	for id, want := range map[string]bool{"P-12": true, "TT-101": true, "": false, "P 12": false, "a;drop": false} {
		if got := validID(id); got != want {
			t.Errorf("validID(%q) = %v", id, got)
		}
	}
}
