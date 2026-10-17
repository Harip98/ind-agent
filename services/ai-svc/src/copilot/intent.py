"""Question -> asset, time range and alarm types. Rules first; the LLM only names an asset."""

import re
from dataclasses import dataclass
from datetime import date, datetime, time, timedelta
from zoneinfo import ZoneInfo

from copilot.datasvc import AssetInfo
from copilot.llm import LLM
from copilot.ollama import OllamaError

MAX_DAYS = 30  # data-svc alarm window limit
_KIND_PREFIX = {"air compressor": "AC", "compressor": "AC", "ac": "AC", "cooling loop": "CL",
                "loop": "CL", "cl": "CL", "pump": "P", "p": "P"}
_ASSET = re.compile(r"\b(air\s+compressor|compressor|cooling\s+loop|loop|pump|ac|cl|p)[\s-]*0*(\d{1,3})\b", re.I)
_GENERIC = re.compile(r"\b(air\s+compressor|compressor|cooling\s+loop|pump)s?\b", re.I)
_MONTHS = {m: i for i, m in enumerate("jan feb mar apr may jun jul aug sep oct nov dec".split(), start=1)}
_MONTH = (r"(jan(?:uary)?|feb(?:ruary)?|mar(?:ch)?|apr(?:il)?|may|june?|july?|aug(?:ust)?"
          r"|sep(?:t(?:ember)?)?|oct(?:ober)?|nov(?:ember)?|dec(?:ember)?)")
_DAY_MONTH = re.compile(r"\b(\d{1,2})(?:st|nd|rd|th)?\s+" + _MONTH + r"\b")
_MONTH_DAY = re.compile(r"\b" + _MONTH + r"\s+(\d{1,2})(?:st|nd|rd|th)?\b")
ASSET_SCHEMA = {"type": "object", "properties": {"asset_mention": {"type": ["string", "null"]}},
                "required": ["asset_mention"]}
_ALARM_WORDS = [
    ("high_temp", re.compile(r"\b(temperature|temp|overheat\w*|hot)\b")),
    ("high_vibration", re.compile(r"\b(vibrat\w*|shak\w*)")),
    ("low_flow", re.compile(r"\bflow\b")),
    ("low_pressure", re.compile(r"\bpressure\b")),
    ("high_pressure", re.compile(r"\bpressure\b")),
    ("high_current", re.compile(r"\b(motor current|current draw|amps|overload\w*)\b")),
    ("bad_quality", re.compile(r"\b(bad data|bad quality|no data|signal|flat-?line\w*)\b")),
]


@dataclass(frozen=True)
class TimeRange:
    start: datetime
    end: datetime
    label: str
    assumed: bool


@dataclass(frozen=True)
class AssetMatch:
    asset: AssetInfo | None
    options: tuple[AssetInfo, ...] = ()


def alarm_types(question: str) -> tuple[str, ...]:
    q = question.lower()
    return tuple(t for t, pattern in _ALARM_WORDS if pattern.search(q))


def _prefix(kind: str) -> str:
    return _KIND_PREFIX[re.sub(r"\s+", " ", kind.lower())]


def find_asset(text: str, assets: list[AssetInfo]) -> AssetMatch:
    by_id = {a.id.upper(): a for a in assets}
    for m in _ASSET.finditer(text):
        aid = f"{_prefix(m.group(1))}-{int(m.group(2))}"
        if aid in by_id:
            return AssetMatch(by_id[aid])
    if _ASSET.search(text):
        return AssetMatch(None)
    lowered = text.lower()
    for a in assets:
        if a.name.lower() in lowered:
            return AssetMatch(a)
    if g := _GENERIC.search(text):
        prefix = _prefix(g.group(1))
        options = tuple(a for a in assets if a.id.upper().startswith(prefix + "-"))
        return AssetMatch(options[0]) if len(options) == 1 else AssetMatch(None, options)
    return AssetMatch(None)


def resolve_asset(question: str, assets: list[AssetInfo], llm: LLM | None = None) -> tuple[AssetMatch, str]:
    match = find_asset(question, assets)
    if match.asset or match.options or _ASSET.search(question) or llm is None:
        return match, "rules" if (match.asset or match.options) else "none"
    try:
        mention = llm.generate_json(
            "Extract the equipment the user asks about, exactly as written. Use null if none.",
            question, ASSET_SCHEMA).data.get("asset_mention")
    except (OllamaError, AttributeError):
        return AssetMatch(None), "none"
    if not mention:
        return AssetMatch(None), "none"
    match = find_asset(str(mention), assets)
    return match, "llm" if match.asset else "none"


def _day(d: date, zone: ZoneInfo) -> tuple[datetime, datetime]:
    start = datetime.combine(d, time(0), zone)
    return start, start + timedelta(days=1)


def _explicit_date(q: str, today: date) -> date | None:
    if m := re.search(r"\b(\d{4})-(\d{2})-(\d{2})\b", q):
        try:
            return date(int(m.group(1)), int(m.group(2)), int(m.group(3)))
        except ValueError:
            return None
    found = [(m.group(1), m.group(2)) for m in _DAY_MONTH.finditer(q)]
    found += [(m.group(2), m.group(1)) for m in _MONTH_DAY.finditer(q)]
    for day_s, mon_s in found:
        mon_s = mon_s[:3]
        if mon_s not in _MONTHS:
            continue
        try:
            candidate = date(today.year, _MONTHS[mon_s], int(day_s))
        except ValueError:
            continue
        return candidate if candidate <= today else candidate.replace(year=today.year - 1)
    return None


def resolve_time(question: str, as_of: datetime, timezone: str) -> TimeRange:
    zone = ZoneInfo(timezone)
    q = question.lower()
    local_now = as_of.astimezone(zone)
    today = local_now.date()

    def rng(start: datetime, end: datetime, label: str, assumed: bool = False) -> TimeRange:
        end = min(end, local_now)
        if start >= end:  # a future date: fall back to the default window and say so
            return rng(as_of - timedelta(days=7), local_now, "last 7 days", True)
        return TimeRange(start.astimezone(as_of.tzinfo), end.astimezone(as_of.tzinfo), label, assumed)

    if "yesterday" in q:
        return rng(*_day(today - timedelta(days=1), zone), "yesterday")
    if "last night" in q:
        start = datetime.combine(today - timedelta(days=1), time(18), zone)
        return rng(start, start + timedelta(hours=12), "last night")
    if "today" in q or "this morning" in q:
        return rng(_day(today, zone)[0], local_now, "today")
    if m := re.search(r"\b(\d{1,3})\s+days?\s+ago\b", q):
        n = int(m.group(1))
        return rng(*_day(today - timedelta(days=n), zone), f"{n} days ago")
    # Rolling windows are measured in elapsed time (UTC), not wall-clock days, so a DST change
    # never makes "last 30 days" exceed the data-svc 30-day limit.
    if m := re.search(r"\b(?:last|past)\s+(\d{1,3})\s+days?\b", q):
        n = min(int(m.group(1)), MAX_DAYS)
        return rng(as_of - timedelta(days=n), local_now, f"last {n} days")
    if re.search(r"\b(?:last|past|this)\s+week\b", q):
        return rng(as_of - timedelta(days=7), local_now, "last 7 days")
    if d := _explicit_date(q, today):
        return rng(*_day(d, zone), d.isoformat())
    return rng(as_of - timedelta(days=7), local_now, "last 7 days", assumed=True)
