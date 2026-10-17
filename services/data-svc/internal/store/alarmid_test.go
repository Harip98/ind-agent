package store

import (
	"testing"
	"time"
)

func TestAlarmIDIsOpaqueAndStable(t *testing.T) {
	ts := time.Date(2026, 10, 16, 3, 31, 0, 0, time.FixedZone("IST", 19800))
	if got := AlarmID("P-12", "TT-101", ts); got != "P-12:TT-101:20261015T2201Z" {
		t.Fatalf("AlarmID = %q", got)
	}
}
