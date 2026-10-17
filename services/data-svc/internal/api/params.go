package api

import (
	"fmt"
	"math"
	"net/http"
	"net/url"
	"regexp"
	"time"
)

const (
	MaxAlarmWindow  = 30 * 24 * time.Hour
	MaxAlarmRows    = 500
	MaxSensorWindow = 72 * time.Hour
	MaxPoints       = 2000
	minBucket       = 60
)

var idPattern = regexp.MustCompile(`^[A-Za-z0-9_-]{1,32}$`)

func validID(s string) bool { return idPattern.MatchString(s) }

func parseWindow(q url.Values, max time.Duration) (time.Time, time.Time, *apiError) {
	bad := func(msg string) *apiError { return &apiError{http.StatusBadRequest, "invalid_range", msg} }
	from, err := time.Parse(time.RFC3339, q.Get("from"))
	if err != nil {
		return time.Time{}, time.Time{}, bad("from must be an RFC 3339 timestamp")
	}
	to, err := time.Parse(time.RFC3339, q.Get("to"))
	if err != nil {
		return time.Time{}, time.Time{}, bad("to must be an RFC 3339 timestamp")
	}
	if !to.After(from) {
		return time.Time{}, time.Time{}, bad("to must be after from")
	}
	if to.Sub(from) > max {
		return time.Time{}, time.Time{}, &apiError{http.StatusBadRequest, "window_too_large",
			fmt.Sprintf("window must be at most %s", max)}
	}
	return from.UTC(), to.UTC(), nil
}

func bucketSeconds(from, to time.Time) int {
	b := int(math.Ceil(to.Sub(from).Seconds() / MaxPoints))
	if b < minBucket {
		return minBucket
	}
	return b
}
