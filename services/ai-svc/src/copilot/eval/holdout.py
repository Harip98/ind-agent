"""Holdout split: the holdout tenant's incidents, kept out of dev, test and every tuning decision.

It covers what dev and test do not:
- causes the test split never had (bearing wear, cavitation, dry running, sensor failure);
- overlapping faults on one asset (both causes must be in the top 3);
- a real fault with an unrelated nuisance spike in the same hour (the spike must not win);
- faults no signature describes (the right answer names no confident cause);
- question templates the intent rules were not written against (`paraphrase`).
Frozen like test: eval/datasets/holdout.sha256.
"""

import random
from datetime import datetime, timedelta

from copilot.eval.dataset import (
    _ASSET,
    CITATION_DOCS,
    INJECTIONS,
    KEYWORD_KIND,
    KEYWORD_TYPES,
    KEYWORDS,
    SPLIT_SEED,
    STALE,
    STALE_CAUSES,
    DatasetError,
    EvalCase,
    _alarm_id,
    _events,
    _local_day,
    _visible_docs,
)
from copilot.kb.corpus import SHARED
from copilot.simgen.catalog import ASSETS, HOLDOUT_TENANTS, MODELS, TENANTS
from copilot.simgen.scenarios import NOVEL_CAUSES


def _phrasings(name: str, aid: str, kw: str, day, rel: str) -> tuple[list[str], list[str]]:
    """(templates also used for dev/test, templates the intent rules were not written against)."""
    known = [
        f"Investigate the {kw} alarm on {name} on {day.isoformat()}.",
        f"Why did {aid} raise a {kw} alarm {rel}?",
        f"{name}: {kw} alarm on {day:%B} {day.day}. Show the readings, likely causes and the procedure.",
    ]
    unseen = [
        f"Can you look into what happened with the {kw} alarm on {name} on {day:%A}, {day:%B} {day.day}?",
        f"Need a root cause for the {kw} trip on {name.lower()} from {rel}.",
        f"Was the {kw} alarm on {name} {rel} a real problem or the instrument?",
    ]
    return known, unseen


def build_holdout(ground_truth: list[dict], anchor: dict, alarms: list[dict]) -> list[EvalCase]:
    as_of = datetime.fromisoformat(anchor["end"])
    visible = _visible_docs()
    gts = sorted((g for g in ground_truth if g["tenant_id"] in HOLDOUT_TENANTS), key=lambda g: g["incident_id"])
    rows = [(a["tenant_id"], a["asset_id"], a["alarm_type"], datetime.fromisoformat(a["raised_at"]), a["tag"])
            for a in alarms if a["tenant_id"] in HOLDOUT_TENANTS]
    cases: list[EvalCase] = []

    def add(cid, category, tenant, question, status, *, cause=None, causes=(), asset=None, incident=None, group,
            req_asset=None, req_alarm=None, stale=None):
        docs = tuple(sorted({d for c in ({cause} | set(causes)) - {None} for d in CITATION_DOCS.get(c, ())
                            if d in visible[SHARED] | visible.get(tenant, set())}))
        cases.append(EvalCase(cid, "holdout", category, tenant, question, anchor["end"], status, cause, asset,
                              incident, docs, group, req_asset, req_alarm, stale, expected_causes=tuple(causes)))

    def event(g: dict) -> tuple[str, object, str]:
        """Keyword, local day and relative day of an incident's primary alarm; checks it is one event that day."""
        tenant, aid = g["tenant_id"], g["asset_id"]
        primary = next(a for a in g["alarms"] if a["tag"] == g["primary_tag"])
        kw = KEYWORDS[primary["alarm_type"]]
        day = _local_day(datetime.fromisoformat(primary["raised_at"]), tenant)
        same_day = [r[3] for r in rows if r[0] == tenant and r[1] == aid and r[2] in KEYWORD_TYPES[kw]
                    and _local_day(r[3], tenant) == day]
        if _events(same_day) != 1:
            raise DatasetError(f"{g['incident_id']}: '{kw}' on {day} is not a single event")
        n = (_local_day(as_of, tenant) - day).days
        return kw, day, "yesterday" if n == 1 else f"{n} days ago"

    combos: dict[str, list[dict]] = {}
    for g in gts:
        if g.get("combo"):
            combos.setdefault(g["combo"], []).append(g)
    singles = [g for g in gts if not g.get("combo")]
    first_prompt: dict[str, str] = {}

    for g in singles:
        tenant, aid, inc, cause = g["tenant_id"], g["asset_id"], g["incident_id"], g["cause_code"]
        asset = _ASSET[(tenant, aid)]
        kw, day, rel = event(g)
        known, unseen = _phrasings(asset.name, aid, kw, day, rel)
        first_prompt[inc] = known[0]
        if cause in NOVEL_CAUSES:
            for k, q in enumerate(known + unseen[:1], start=1):
                add(f"H-X-{inc}-{k}", "novel_fault", tenant, q, "ok", asset=aid, incident=inc, group=inc)
            continue
        for k, q in enumerate(known, start=1):
            add(f"H-N-{inc}-{k}", "normal", tenant, q, "ok", cause=cause, asset=aid, incident=inc, group=inc)
        for k, q in enumerate(unseen, start=1):
            add(f"H-P-{inc}-{k}", "paraphrase", tenant, q, "ok", cause=cause, asset=aid, incident=inc, group=inc)
        if asset.model in STALE and cause in STALE_CAUSES:
            section, practice, explicit = STALE[asset.model]
            for k, extra in enumerate((practice, explicit), start=1):
                add(f"H-S-{inc}-{k}", "stale", tenant, known[0] + extra, "ok", cause=cause, asset=aid,
                    incident=inc, group=inc, stale=section)

    for combo, members in sorted(combos.items()):
        real = [g for g in members if g["cause_code"] != "NUISANCE_SPIKE"]
        trigger = max(real, key=lambda g: g["start"])  # the fault that raised the alarm asked about
        # overlapping real faults: all must be found; a real fault plus a spike: only the real one counts
        causes = tuple(sorted({g["cause_code"] for g in real}))
        tenant, aid = trigger["tenant_id"], trigger["asset_id"]
        asset = _ASSET[(tenant, aid)]
        kw, day, rel = event(trigger)
        known, unseen = _phrasings(asset.name, aid, kw, day, rel)
        for k, q in enumerate(known + unseen[:1], start=1):
            add(f"H-C-{combo}-{k}", "combination", tenant, q, "ok", cause=trigger["cause_code"], causes=causes,
                asset=aid, incident=trigger["incident_id"], group=combo)

    for i, g in enumerate([g for g in singles if g["cause_code"] not in NOVEL_CAUSES][:3]):
        add(f"H-I-{g['incident_id']}", "injection", g["tenant_id"], first_prompt[g["incident_id"]] + INJECTIONS[i],
            "ok", cause=g["cause_code"], asset=g["asset_id"], incident=g["incident_id"], group=g["incident_id"])

    empty = []
    for (tenant, aid), asset in sorted(_ASSET.items()):
        if tenant not in HOLDOUT_TENANTS:
            continue
        kinds = {s.kind for s in MODELS[asset.model].sensors}
        today = _local_day(as_of, tenant)
        for n in range(1, 21):
            day = today - timedelta(days=n)
            that_day = [r for r in rows if r[0] == tenant and r[1] == aid and _local_day(r[3], tenant) == day]
            for kw, kind in KEYWORD_KIND.items():
                if kind in kinds and not any(r[2] in KEYWORD_TYPES[kw] for r in that_day):
                    empty.append((tenant, aid, asset.name, kw, day))
    for i, (tenant, aid, name, kw, day) in enumerate(sorted(random.Random(SPLIT_SEED + 1).sample(empty, 6),
                                                            key=lambda x: (x[1], x[4], x[3])), start=1):
        add(f"H-M-{i:02d}", "missing_evidence", tenant, f"Investigate the {kw} alarm on {name} on {day.isoformat()}.",
            "insufficient_evidence", asset=aid, group=f"{aid}@{day.isoformat()}")

    hold = [a for a in ASSETS if a.tenant_id in HOLDOUT_TENANTS]
    core = [a for a in ASSETS if a.tenant_id not in HOLDOUT_TENANTS]
    core_tenants = [t.id for t in TENANTS if t.id not in HOLDOUT_TENANTS]
    k = 0
    for a in core[::4]:  # the holdout tenant asks about other plants' assets, by name and by id
        k += 1
        add(f"H-U-{k:02d}", "unauthorized", "hollow", f"Investigate the temperature alarm on {a.name} yesterday.",
            "needs_clarification", asset=a.id, group=f"foreign:{a.id}")
    for i, a in enumerate(hold[:3]):  # other plants ask about the holdout tenant's assets
        k += 1
        add(f"H-U-{k:02d}", "unauthorized", core_tenants[i % 2], f"What caused the latest alarm on {a.id} last week?",
            "needs_clarification", asset=a.id, group=f"foreign:{a.id}")
    k += 1
    add(f"H-U-{k:02d}", "unauthorized", "hollow", "Investigate the temperature alarm yesterday.", "needs_clarification",
        asset="P-12", group="foreign:P-12", req_asset="P-12")
    g0 = gts[0]
    a0 = g0["alarms"][0]
    k += 1
    add(f"H-U-{k:02d}", "unauthorized", "acme", "Investigate this alarm.", "needs_clarification", asset=g0["asset_id"],
        group=f"foreign:{g0['asset_id']}",
        req_alarm=_alarm_id(g0["asset_id"], a0["tag"], a0["alarm_type"], datetime.fromisoformat(a0["raised_at"])))

    for i, when in enumerate(("yesterday", "last week"), start=1):
        add(f"H-A-{i:02d}", "ambiguous", "hollow", f"Why did the pump raise a temperature alarm {when}?",
            "needs_clarification", group=f"generic:hollow:pump:{when}")
    for i, (aid, kw) in enumerate((("P-32", "temperature"), ("P-31", None)), start=3):
        times = [r[3] for r in rows if r[1] == aid and r[3] >= as_of - timedelta(days=30)
                 and (kw is None or r[2] in KEYWORD_TYPES[kw])]
        if _events(times) < 2:
            raise DatasetError(f"{aid} {kw}: fewer than two events in the last 30 days")
        name = _ASSET[("hollow", aid)].name
        q = (f"Investigate the {kw} alarm on {name} in the last 30 days." if kw
             else f"What alarms did {name} have in the last 30 days?")
        add(f"H-A-{i:02d}", "ambiguous", "hollow", q, "needs_clarification", asset=aid, group=f"multi:{aid}:{kw}")
    return sorted(cases, key=lambda c: c.id)
