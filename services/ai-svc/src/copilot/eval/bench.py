"""Operational metrics: latency percentiles, error rate, tokens, memory and recovery after a dependency outage.

Requests go through the real FastAPI app in-process (TestClient), so error mapping (503s, 500s) is measured
too. Chaos phases stop and start a docker compose service between requests and time the recovery.
"""

import ctypes
import json
import shutil
import statistics
import subprocess
import sys
import time
from collections import Counter
from collections.abc import Callable
from dataclasses import dataclass, field
from pathlib import Path

import httpx


def process_memory_mb() -> tuple[float, float]:
    """(current, peak) resident memory of this process in MiB, without psutil."""
    if sys.platform == "win32":
        class Counters(ctypes.Structure):
            _fields_ = [("cb", ctypes.c_ulong), ("PageFaultCount", ctypes.c_ulong),
                        ("PeakWorkingSetSize", ctypes.c_size_t), ("WorkingSetSize", ctypes.c_size_t),
                        ("QuotaPeakPagedPoolUsage", ctypes.c_size_t), ("QuotaPagedPoolUsage", ctypes.c_size_t),
                        ("QuotaPeakNonPagedPoolUsage", ctypes.c_size_t), ("QuotaNonPagedPoolUsage", ctypes.c_size_t),
                        ("PagefileUsage", ctypes.c_size_t), ("PeakPagefileUsage", ctypes.c_size_t)]

        c = Counters()
        c.cb = ctypes.sizeof(c)
        k32 = ctypes.windll.kernel32
        k32.GetCurrentProcess.restype = ctypes.c_void_p  # a 64-bit pseudo-handle; the int default truncates it
        k32.K32GetProcessMemoryInfo.argtypes = [ctypes.c_void_p, ctypes.POINTER(Counters), ctypes.c_ulong]
        if not k32.K32GetProcessMemoryInfo(k32.GetCurrentProcess(), ctypes.byref(c), c.cb):
            raise OSError("GetProcessMemoryInfo failed")
        return c.WorkingSetSize / 2**20, c.PeakWorkingSetSize / 2**20
    fields = {}
    for line in Path("/proc/self/status").read_text().splitlines():
        key, _, value = line.partition(":")
        fields[key] = value.strip()
    kib = lambda k: float(fields.get(k, "0 kB").split()[0])  # noqa: E731
    return kib("VmRSS") / 1024, kib("VmHWM") / 1024


def gpu_memory(ollama_url: str) -> dict:
    """VRAM held by loaded Ollama models, and the whole GPU's use when nvidia-smi exists."""
    out: dict = {}
    try:
        models = httpx.get(f"{ollama_url}/api/ps", timeout=2.0).json().get("models", [])
        out["ollama_models"] = {m["name"]: round(m.get("size_vram", 0) / 2**20) for m in models}
    except (httpx.HTTPError, ValueError):
        out["ollama_models"] = None
    if shutil.which("nvidia-smi"):
        try:
            used, total = subprocess.run(
                ["nvidia-smi", "--query-gpu=memory.used,memory.total", "--format=csv,noheader,nounits"],
                capture_output=True, text=True, timeout=5, check=True).stdout.strip().splitlines()[0].split(", ")
            out["gpu_used_mib"], out["gpu_total_mib"] = int(used), int(total)
        except (subprocess.SubprocessError, ValueError, IndexError):
            pass
    return out


def container_memory(project_dir: Path) -> dict[str, str]:
    """Memory of the compose services (docker stats), e.g. {"postgres": "180MiB"}."""
    try:
        ids = subprocess.run(["docker", "compose", "ps", "-q"], cwd=project_dir, capture_output=True, text=True,
                             timeout=20, check=True).stdout.split()
        if not ids:
            return {}
        lines = subprocess.run(["docker", "stats", "--no-stream", "--format", "{{json .}}", *ids],
                               capture_output=True, text=True, timeout=60, check=True).stdout.splitlines()
    except (OSError, subprocess.SubprocessError):
        return {}
    out = {}
    for line in lines:
        row = json.loads(line)
        name = row["Name"].removeprefix("ind-agent-").rsplit("-", 1)[0]
        out[name] = row["MemUsage"].split(" / ")[0]
    return out


@dataclass
class Sample:
    phase: str
    case_id: str
    code: int
    error: str | None
    status: str | None
    ms: int
    tokens: int | None
    t: float  # seconds since the run started


@dataclass
class Run:
    mode: str
    samples: list[Sample] = field(default_factory=list)
    events: list[tuple[float, str]] = field(default_factory=list)  # (t, "stopped data-svc")
    memory: dict = field(default_factory=dict)


def pct(values: list[float], q: float) -> int | None:
    s = sorted(values)
    return int(s[min(len(s) - 1, int(round(q * (len(s) - 1))))]) if s else None


def summarize_run(run: Run) -> dict:
    phases: dict[str, dict] = {}
    for phase in dict.fromkeys(s.phase for s in run.samples):
        rows = [s for s in run.samples if s.phase == phase]
        ok = [s for s in rows if s.code == 200]
        tokens = [s.tokens for s in rows if s.tokens]
        phases[phase] = {
            "n": len(rows), "ok": len(ok), "error_rate": round(1 - len(ok) / len(rows), 3),
            "codes": dict(Counter(f"{s.code} {s.error}" if s.error else str(s.code) for s in rows)),
            "statuses": dict(Counter(s.status for s in ok)),
            "p50": pct([s.ms for s in rows], 0.5), "p95": pct([s.ms for s in rows], 0.95),
            "p99": pct([s.ms for s in rows], 0.99), "max": max(s.ms for s in rows),
            "tokens_mean": round(statistics.fmean(tokens)) if tokens else None,
        }
    return {"mode": run.mode, "phases": phases, "recovery": recovery(run), "memory": run.memory}


def recovery(run: Run) -> list[dict]:
    """For each 'started X' event: seconds until the first 200 before the next event, and how many requests failed
    meanwhile. A later phase's outage is never credited to an earlier restart; None = not recovered in its phase."""
    out = []
    events = sorted(run.events)
    for i, (t_start, what) in enumerate(events):
        if not what.startswith("started "):
            continue
        t_end = events[i + 1][0] if i + 1 < len(events) else float("inf")
        after = [s for s in run.samples if t_start <= s.t < t_end]
        first_ok = next((s for s in after if s.code == 200), None)
        failed = [s for s in after if first_ok is None or s.t < first_ok.t]
        out.append({"service": what.removeprefix("started "), "seconds_to_first_success":
                    None if first_ok is None else round(first_ok.t - t_start, 1), "failed_meanwhile": len(failed)})
    return out


def run_bench(post: Callable[[dict], httpx.Response], questions: list[dict], mode: str,
              phases: list[tuple[str, int, Callable[[], None] | None]], memory_probe: Callable[[], dict]) -> Run:
    """`phases`: (name, requests, action run before the phase). Questions are cycled in order."""
    run = Run(mode)
    t0 = time.perf_counter()
    k = 0
    for name, n, action in phases:
        if action is not None:
            label = action()
            if label:
                run.events.append((time.perf_counter() - t0, label))
        for _ in range(n):
            q = questions[k % len(questions)]
            k += 1
            start = time.perf_counter()
            r = post({"question": q["question"], "as_of": q["as_of"], "mode": mode,
                      **({"asset_id": q["asset_id"]} if q.get("asset_id") else {}),
                      **({"alarm_id": q["alarm_id"]} if q.get("alarm_id") else {})} | {"_tenant": q["tenant"]})
            ms = int((time.perf_counter() - start) * 1000)
            body = r.json() if r.headers.get("content-type", "").startswith("application/json") else {}
            llm = body.get("llm") or {}
            run.samples.append(Sample(name, q["id"], r.status_code, (body.get("error") or {}).get("code"),
                                      body.get("status"), ms,
                                      (llm.get("prompt_tokens", 0) + llm.get("completion_tokens", 0)) or None,
                                      round(time.perf_counter() - t0, 2)))
    run.memory = memory_probe()
    return run


def _recovered(seconds: float | None) -> str:
    return "not within its phase" if seconds is None else str(seconds)


def render_bench(summaries: list[dict], notes: list[str], title: str) -> str:
    lines = [f"# {title}", ""]
    for s in summaries:
        lines += [f"## Mode `{s['mode']}`", "",
                  "| Phase | Requests | Error rate | Responses | p50 ms | p95 ms | p99 ms | max ms | Tokens/request |",
                  "|---|---|---|---|---|---|---|---|---|"]
        for phase, p in s["phases"].items():
            codes = ", ".join(f"{k} ×{v}" for k, v in p["codes"].items())
            statuses = ", ".join(f"{k} ×{v}" for k, v in p["statuses"].items())
            lines.append(f"| {phase} | {p['n']} | {p['error_rate']:.0%} | {codes}" + (f" ({statuses})" if statuses
                         else "") + f" | {p['p50']} | {p['p95']} | {p['p99']} | {p['max']} | "
                         f"{p['tokens_mean'] or '–'} |")
        if s["recovery"]:
            lines += ["", "| Restarted | Seconds to first success | Requests failed meanwhile |", "|---|---|---|"]
            lines += [f"| {r['service']} | {_recovered(r['seconds_to_first_success'])} | {r['failed_meanwhile']} |"
                      for r in s["recovery"]]
        m = s["memory"]
        if m:
            lines += ["", f"**Memory after the run:** ai-svc process {m['rss_mib']:.0f} MiB (peak {m['peak_mib']:.0f} MiB)"
                      + (f"; GPU {m['gpu']['gpu_used_mib']} / {m['gpu']['gpu_total_mib']} MiB"
                         if m.get("gpu", {}).get("gpu_used_mib") else "")
                      + ("; Ollama models in VRAM: " + ", ".join(f"{k} {v} MiB" for k, v in
                                                                   m["gpu"]["ollama_models"].items())
                         if m.get("gpu", {}).get("ollama_models") else "")
                      + ("; containers: " + ", ".join(f"{k} {v}" for k, v in m["containers"].items())
                         if m.get("containers") else "") + "."]
        lines.append("")
    lines += ["## Method", ""] + [f"- {n}" for n in notes]
    return "\n".join(lines) + "\n"
