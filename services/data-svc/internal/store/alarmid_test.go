package store

import (
	"testing"
	"time"
)

func TestAlarmIDIsOpaqueAndStable(t *testing.T) {
	ts := time.Date(2026, 10, 16, 3, 31, 5, 0, time.FixedZone("IST", 19800))
	if got := AlarmID("P-12", "TT-101", "high_temp", ts); got != "P-12:TT-101:high_temp:20261015T220105Z" {
		t.Fatalf("AlarmID = %q", got)
	}
}

func TestAlarmIDDistinguishesTypesInTheSameMinute(t *testing.T) {
	ts := time.Date(2026, 10, 16, 3, 31, 0, 0, time.UTC)
	if AlarmID("P-11", "TT-101", "bad_quality", ts) == AlarmID("P-11", "TT-101", "high_temp", ts) {
		t.Fatal("alarms of different types at the same instant share an id")
	}
}
