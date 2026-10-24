import httpx

from copilot.eval.bench import (
    Run,
    Sample,
    pct,
    process_memory_mb,
    recovery,
    render_bench,
    run_bench,
    summarize_run,
)


def test_process_memory_is_positive_and_peak_is_at_least_current():
    rss, peak = process_memory_mb()
    assert 0 < rss <= peak


def test_pct():
    assert pct([], 0.5) is None
    assert pct([10, 20, 30, 40], 0.5) == 30 and pct([10, 20, 30, 40], 0.95) == 40


def _s(phase, code, t, error=None, ms=100):
    return Sample(phase, "c", code, error, "ok" if code == 200 else None, ms, 1000 if code == 200 else None, t)


def test_summary_counts_errors_and_recovery_time():
    run = Run("template", [_s("steady", 200, 0.5), _s("steady", 200, 1.0),
                           _s("down", 503, 2.0, "data_unavailable"), _s("down", 503, 2.5, "data_unavailable"),
                           _s("up", 503, 3.2, "data_unavailable"), _s("up", 200, 6.0), _s("up", 200, 6.5)],
              events=[(1.5, "stopped data-svc"), (3.0, "started data-svc")])
    s = summarize_run(run)
    assert s["phases"]["steady"]["error_rate"] == 0 and s["phases"]["down"]["error_rate"] == 1
    assert s["phases"]["down"]["codes"] == {"503 data_unavailable": 2}
    assert s["recovery"] == [{"service": "data-svc", "seconds_to_first_success": 3.0, "failed_meanwhile": 1}]
    assert "| data-svc | 3.0 | 1 |" in render_bench([s], ["n"], "t")


def test_recovery_is_bounded_by_the_next_event():
    run = Run("template", [_s("data-svc restarted", 503, 10.5, "data_unavailable"),
                           _s("postgres stopped", 503, 25.0, "data_unavailable"),
                           _s("postgres restarted", 200, 33.0)],
              events=[(10.0, "started data-svc"), (19.9, "stopped postgres"), (30.0, "started postgres")])
    assert recovery(run) == [{"service": "data-svc", "seconds_to_first_success": None, "failed_meanwhile": 1},
                             {"service": "postgres", "seconds_to_first_success": 3.0, "failed_meanwhile": 0}]
    assert "| data-svc | not within its phase | 1 |" in render_bench([summarize_run(run)], ["n"], "t")


def test_run_bench_cycles_questions_and_runs_phase_actions():
    seen, actions = [], []

    def post(body):
        seen.append(body["question"])
        return httpx.Response(200, json={"status": "ok", "llm": {"prompt_tokens": 5, "completion_tokens": 2}})

    qs = [{"id": "a", "question": "q1", "as_of": "x", "tenant": "acme"},
          {"id": "b", "question": "q2", "as_of": "x", "tenant": "acme"}]
    run = run_bench(post, qs, "llm", [("p1", 3, None), ("p2", 1, lambda: actions.append(1) or "stopped x")],
                    lambda: {"rss_mib": 1})
    assert seen == ["q1", "q2", "q1", "q2"] and actions == [1]
    assert [s.phase for s in run.samples] == ["p1", "p1", "p1", "p2"] and run.samples[0].tokens == 7
    assert run.events[0][1] == "stopped x" and run.memory == {"rss_mib": 1}
