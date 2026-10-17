"use strict";
// Incident copilot UI. Everything from the server is rendered with textContent / DOM nodes:
// citations contain untrusted document text (including a deliberate prompt-injection fixture).

const $ = (id) => document.getElementById(id);
const state = { dev: false, tokens: {}, charts: [], lastQuestion: "" };

const EXAMPLES = {
  acme: [
    "Investigate yesterday's temperature alarm on Pump 12. Show the readings, likely causes and the procedure.",
    "Why did the pump raise a temperature alarm yesterday?",
    "What caused the bad data alarm on Pump 11 2 days ago?",
    "Investigate the vibration alarm on Pump 12 yesterday.",
  ],
  globex: [
    "Investigate the vibration alarm on Pump 20 yesterday.",
    "Why was Air Compressor 2 running hot 2 days ago?",
    "Investigate the temperature alarm on Pump 12 yesterday.",
  ],
};
const STATUS_TEXT = {
  ok: "Investigation complete",
  needs_clarification: "Needs your input",
  insufficient_evidence: "Not enough evidence",
  degraded: "Report written from the evidence only (the language model's draft was rejected by the verifier)",
};

function el(tag, opts = {}, children = []) {
  const node = document.createElement(tag);
  if (opts.cls) node.className = opts.cls;
  if (opts.text !== undefined && opts.text !== null) node.textContent = String(opts.text);
  for (const [k, v] of Object.entries(opts.attrs || {})) node.setAttribute(k, v);
  for (const child of children) if (child) node.append(child);
  return node;
}

function cssVar(name) {
  return getComputedStyle(document.documentElement).getPropertyValue(name).trim();
}

async function init() {
  try {
    const r = await fetch("/dev/defaults");
    if (r.ok) {
      const d = await r.json();
      state.dev = true;
      for (const t of d.tenants) $("tenant").append(el("option", { text: t, attrs: { value: t } }));
      if (d.as_of) $("as-of").value = d.as_of;
    }
  } catch (_) { /* not in dev mode */ }
  if (!state.dev) {
    $("tenant-field").classList.add("hidden");
    $("token-field").classList.remove("hidden");
  }
  if (!$("as-of").value) $("as-of").value = new Date().toISOString().slice(0, 19) + "Z";
  $("tenant").addEventListener("change", renderExamples);
  $("ask").addEventListener("submit", (e) => { e.preventDefault(); ask(); });
  $("question").addEventListener("keydown", (e) => {
    if (e.key === "Enter" && (e.ctrlKey || e.metaKey)) { e.preventDefault(); ask(); }
  });
  renderExamples();
}

function renderExamples() {
  const box = $("examples");
  box.replaceChildren();
  const list = EXAMPLES[$("tenant").value] || EXAMPLES.acme;
  for (const q of list) {
    const b = el("button", { text: q, attrs: { type: "button" } });
    b.addEventListener("click", () => { $("question").value = q; ask(); });
    box.append(b);
  }
}

async function token() {
  if (!state.dev) return $("token").value.trim();
  const tenant = $("tenant").value;
  const cached = state.tokens[tenant];
  if (cached && cached.until > Date.now()) return cached.value;
  const r = await fetch("/dev/token", { method: "POST", headers: { "Content-Type": "application/json" },
                                         body: JSON.stringify({ tenant }) });
  if (!r.ok) throw new Error("Could not get a demo token for this plant.");
  const value = (await r.json()).token;
  state.tokens[tenant] = { value, until: Date.now() + 50 * 60 * 1000 };
  return value;
}

function notice(text, kind = "info", action = null) {
  const n = $("notice");
  n.replaceChildren(el("span", { text }));
  if (action) n.append(action);
  n.className = "notice" + (kind === "error" ? " error" : "");
}

function clearNotice() { $("notice").className = "notice hidden"; }

async function ask(extra = {}, modeOverride = null) {
  const question = $("question").value.trim();
  if (!question) { $("question").focus(); return; }
  state.lastQuestion = question;
  const mode = modeOverride || $("mode").value;
  const btn = $("submit");
  btn.disabled = true;
  btn.textContent = "Investigating…";
  notice(mode === "llm" ? "Investigating. The language model writes the report locally; this takes about a minute."
                        : "Investigating…");
  try {
    const body = { question, mode, as_of: $("as-of").value.trim() || null, ...extra };
    const r = await fetch("/investigate", {
      method: "POST",
      headers: { "Content-Type": "application/json", Authorization: `Bearer ${await token()}` },
      body: JSON.stringify(body),
    });
    const trace = r.headers.get("X-Trace-Id") || "";
    const data = await r.json().catch(() => ({}));
    if (!r.ok) return showError(r.status, data, trace, extra);
    clearNotice();
    render(data);
  } catch (err) {
    notice(err.message || "The request failed. Check that the service is running.", "error");
  } finally {
    btn.disabled = false;
    btn.textContent = "Investigate";
  }
}

function showError(status, data, trace, extra) {
  const code = data && data.error ? data.error.code : "";
  const ref = trace ? ` (trace ${trace})` : "";
  if (status === 401) return notice("Your access token is missing, invalid or expired." + ref, "error");
  if (status === 422) return notice("The question must be between 1 and 1000 characters." + ref, "error");
  if (status === 429) return notice("Too many requests. Try again in a second." + ref, "error");
  if (code === "llm_unavailable") {
    const b = el("button", { text: "Write it from the evidence only", attrs: { type: "button" } });
    b.addEventListener("click", () => ask(extra, "template"));
    return notice("The language model is not running." + ref, "error", b);
  }
  if (code === "data_unavailable") return notice("The plant data service is not reachable." + ref, "error");
  return notice(`The investigation failed (HTTP ${status}).` + ref, "error");
}

function refChips(refs) {
  const box = el("span", { cls: "refs" });
  for (const r of refs || []) {
    const b = el("button", { cls: "ref", text: r, attrs: { type: "button", "aria-label": `Show ${r}` } });
    b.addEventListener("click", () => jumpTo(r));
    box.append(b);
  }
  return box;
}

function jumpTo(ref) {
  const target = document.getElementById(`ref-${ref}`);
  if (!target) return;
  if (target.tagName === "LI" && target.querySelector("details")) target.querySelector("details").open = true;
  target.scrollIntoView({ behavior: matchMedia("(prefers-reduced-motion: reduce)").matches ? "auto" : "smooth",
                          block: "center" });
  target.classList.add("flash");
  setTimeout(() => target.classList.remove("flash"), 1600);
}

function fmtTime(iso) {
  if (!iso) return "";
  const d = new Date(iso);
  return d.toLocaleString(undefined, { dateStyle: "medium", timeStyle: "short" });
}

function render(data) {
  for (const c of state.charts) c.destroy();
  state.charts = [];
  const out = $("result");
  out.replaceChildren();
  $("empty").classList.add("hidden");
  out.classList.remove("hidden");

  const q = data.query || {};
  const statusLine = el("p", { cls: "status" }, [el("strong", { text: STATUS_TEXT[data.status] || data.status })]);
  if (q.asset_id) statusLine.append(`. ${q.asset_name || q.asset_id} (${q.asset_id}), ${q.time_label || ""}.`);
  out.append(statusLine);
  for (const a of q.assumptions || []) out.append(el("p", { cls: "status", text: a }));

  if (data.clarification) {
    const card = el("div", { cls: "clarify" }, [el("p", { text: data.clarification.message })]);
    const opts = el("div", { cls: "options" });
    for (const o of data.clarification.options || []) {
      const b = el("button", { text: o.label, attrs: { type: "button" } });
      b.addEventListener("click", () => ask(o.alarm_id ? { alarm_id: o.alarm_id } : { asset_id: o.asset_id }));
      opts.append(b);
    }
    card.append(opts);
    out.append(card);
  }

  const alarm = data.evidence && data.evidence.alarm;
  if (alarm) {
    const strip = el("div", { cls: "annunciator" + (alarm.cleared_at ? " cleared" : "") }, [
      el("span", { cls: "tag", text: alarm.tag }),
      el("span", { cls: "what", text: alarm.message }),
      el("span", { cls: "detail", text: `Raised ${fmtTime(alarm.raised_at)}` +
        (alarm.cleared_at ? `, cleared ${fmtTime(alarm.cleared_at)}` : ", still active at the end of the data") }),
    ]);
    out.append(strip);
  }

  const report = data.report;
  if (!report) return footer(out, data);
  const sheet = el("div", { cls: "sheet" });
  const left = el("div");
  const right = el("div");
  sheet.append(left, right);
  out.append(sheet);

  left.append(el("section", {}, [el("h2", { text: "Summary" }), el("p", { cls: "summary", text: report.summary })]));

  if ((report.possible_explanations || []).length) {
    const list = el("ol", { cls: "causes" });
    for (const e of report.possible_explanations) {
      const meter = el("span", { cls: "meter", attrs: { title: `signature score ${e.score}` } },
                       [el("span", { attrs: { style: `width:${Math.round((e.score || 0) * 100)}%` } })]);
      list.append(el("li", {}, [
        el("div", { cls: "head" }, [el("span", { cls: "label", text: e.label }),
                                    el("span", { cls: "confidence", text: `${e.confidence} confidence` }), meter]),
        el("p", {}, [e.text, refChips(e.refs)]),
      ]));
    }
    left.append(el("section", {}, [el("h2", { text: "Possible causes, most likely first" }), list]));
  }

  if (report.recommendation) {
    left.append(el("section", { cls: "workorder" }, [
      el("h2", { text: "Recommended next step" }),
      el("p", {}, [report.recommendation.text, refChips(report.recommendation.refs)]),
    ]));
  }

  if ((report.insufficient_evidence || []).length) {
    const ul = el("ul", { cls: "warnings" });
    for (const w of report.insufficient_evidence) ul.append(el("li", { text: w }));
    left.append(el("section", {}, [el("h2", { text: "Check before acting" }), ul]));
  }

  if ((report.observations || []).length) {
    const ul = el("ul", { cls: "observations" });
    for (const o of report.observations) {
      ul.append(el("li", { attrs: { id: `ref-${o.id}` } }, [el("span", { cls: "oid", text: o.id }), el("span", { text: o.text })]));
    }
    left.append(el("section", {}, [el("h2", { text: "What the readings show" }), ul]));
  }

  const series = (data.evidence && data.evidence.series) || {};
  if (alarm && Object.keys(series).length) {
    const grid = el("div", { cls: "trends" });
    right.append(el("section", {}, [el("h2", { text: "Readings, 6 h before to 2 h after the alarm" }), grid]));
    Object.keys(series).forEach((tag, i) => grid.append(trend(tag, series[tag], alarm, i === 0)));
  }

  if ((data.citations || []).length) {
    const ul = el("ul", { cls: "docs" });
    for (const c of data.citations) {
      const summary = el("summary", {}, [
        el("span", { cls: "did", text: c.ref }),
        el("span", { text: c.heading }),
        c.stale ? el("span", { cls: "badge", text: "Superseded" }) : null,
        el("div", { cls: "src", text: `${c.doc_id} v${c.version} §${c.section}, ${c.title}` }),
      ]);
      ul.append(el("li", { cls: c.stale ? "stale" : "", attrs: { id: `ref-${c.ref}` } }, [
        el("details", {}, [summary, el("p", { cls: "body", text: c.content || "" })]),
      ]));
    }
    right.append(el("section", {}, [el("h2", { text: "Procedures consulted" }), ul]));
  }
  footer(out, data);
}

function trend(tag, points, alarm, primary) {
  const box = el("div", { cls: "trend" + (primary ? " primary" : "") });
  box.append(el("h3", {}, [el("span", { text: tag }), el("span", { cls: "unit", text: primary ? "alarmed signal" : "" })]));
  if (typeof Chart === "undefined") {
    box.append(el("p", { cls: "nochart", text: "Charts need Chart.js, which could not be loaded." }));
    return box;
  }
  const holder = el("div", { cls: "canvas" });
  const canvas = el("canvas", { attrs: { role: "img", "aria-label": `${tag} readings around the alarm` } });
  holder.append(canvas);
  box.append(holder);
  const t0 = new Date(alarm.raised_at).getTime();
  const xy = (key) => points.filter((p) => p[key] !== null).map((p) => ({ x: (new Date(p.ts).getTime() - t0) / 60000, y: p[key] }));
  const line = primary ? cssVar("--alarm") : cssVar("--trend");
  const datasets = [
    { data: xy("min"), borderWidth: 0, pointRadius: 0, fill: false },
    { data: xy("max"), borderWidth: 0, pointRadius: 0, fill: "-1", backgroundColor: cssVar("--band") },
    { data: xy("avg"), borderColor: line, borderWidth: primary ? 2 : 1.4, pointRadius: 0, fill: false },
  ];
  if (primary && alarm.limit_value !== null && alarm.limit_value !== undefined) {
    const xs = datasets[2].data.map((p) => p.x);
    datasets.push({ data: [{ x: Math.min(...xs), y: alarm.limit_value }, { x: Math.max(...xs), y: alarm.limit_value }],
                    borderColor: cssVar("--alarm"), borderDash: [5, 4], borderWidth: 1, pointRadius: 0, fill: false });
  }
  const alarmLine = {
    id: "alarmLine",
    afterDatasetsDraw(chart) {
      const x = chart.scales.x.getPixelForValue(0);
      const { top, bottom } = chart.chartArea;
      const ctx = chart.ctx;
      ctx.save();
      ctx.strokeStyle = cssVar("--alarm");
      ctx.lineWidth = 1;
      ctx.beginPath();
      ctx.moveTo(x, top);
      ctx.lineTo(x, bottom);
      ctx.stroke();
      ctx.restore();
    },
  };
  state.charts.push(new Chart(canvas, {
    type: "line",
    data: { datasets },
    plugins: [alarmLine],
    options: {
      animation: false, parsing: false, normalized: true, maintainAspectRatio: false,
      plugins: { legend: { display: false }, tooltip: { enabled: false } },
      scales: {
        x: { type: "linear", min: -360, max: 120, grid: { color: cssVar("--line") },
             ticks: { color: cssVar("--muted"), stepSize: primary ? 60 : 120, maxRotation: 0, autoSkip: true,
                      callback: (v) => (v === 0 ? "alarm" : `${v > 0 ? "+" : ""}${v / 60} h`) } },
        y: { grid: { color: cssVar("--line") }, ticks: { color: cssVar("--muted"), maxTicksLimit: primary ? 6 : 4 } },
      },
    },
  }));
  return box;
}

function footer(out, data) {
  const t = data.timings_ms || {};
  const parts = Object.entries(t).map(([k, v]) => `${k} ${v} ms`).join(", ");
  const llm = data.llm ? ` Language model ${data.llm.model}: ${data.llm.attempts} attempt(s), ${data.llm.prompt_tokens + data.llm.completion_tokens} tokens.` : "";
  out.append(el("p", { cls: "footer" }, [`Trace `, el("code", { text: data.trace_id }), `. Mode ${data.mode}. Timings: ${parts}.${llm}`]));
}

init();
