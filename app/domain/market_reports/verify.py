"""Checks a model-written market report before publication — spec 03-ecrans/UI-18
« Vérification avant publication » (ADR-030). Returns the list of problems (empty = publishable).

The key rule: every number of the text must be one of the facts (or of the previous reports),
possibly rounded to fewer decimals. The model may describe, never compute or invent.
"""

from __future__ import annotations

import re
from typing import Any

from app.domain.market_reports.facts import TAGS

SECTIONS = ("regime", "leaders", "volatility", "changes", "bots", "watch")
MIN_WORDS, MAX_WORDS = 300, 900
MIN_CHARTS, MAX_CHARTS = 3, 5
MAX_MODEL_TAGS = 2
CHART_MARK = re.compile(r"\{\{chart:([a-z0-9-]+)\}\}")
NUMBER = re.compile(r"(?<![\w.])[-+−]?\d{1,3}(?:,\d{3})+(?:\.\d+)?(?![\w])|(?<![\w.])[-+−]?\d+(?:\.\d+)?")
TIME = re.compile(r"\b\d{1,2}:\d{2}\b")
DATE = re.compile(r"\b\d{4}-\d{2}-\d{2}\b")
ALWAYS = {1, 4, 7, 24, 60, 240, 1440}          # cadences and the 7-day reference
SMALL_INT = 31                                  # counts, days of the month, hours
FORBIDDEN = [
    (re.compile(r"\b(buy|sell|short|long)\s+(now|it|them|this|these|the dip)\b", re.I), "trading call"),
    (re.compile(r"\bprice target\b|\btarget price\b", re.I), "price target"),
    (re.compile(r"\bwill\s+(reach|hit|rise|fall|climb|drop|rally|crash|go)\b", re.I), "price prediction"),
    (re.compile(r"\bguarantee", re.I), "guarantee"),
    (re.compile(r"\byou should\b|\bwe recommend\b|\bwe advise\b", re.I), "advice"),
    (re.compile(r"<[a-zA-Z/][^>]*>"), "HTML"),
    (re.compile(r"https?://|www\.", re.I), "external link"),
]


def _walk(value: Any, out: list[str]) -> None:
    if isinstance(value, dict):
        for v in value.values():
            _walk(v, out)
    elif isinstance(value, (list, tuple)):
        for v in value:
            _walk(v, out)
    elif value is not None and not isinstance(value, bool):
        out.append(str(value))


def allowed_values(facts: dict[str, Any], previous: list[dict[str, Any]]) -> tuple[set[float], set[str], set[str]]:
    """(numbers, times HH:MM, dates YYYY-MM-DD) found anywhere in the facts and previous reports."""
    texts: list[str] = []
    _walk(facts, texts)
    _walk(previous, texts)
    numbers: set[float] = set()
    times: set[str] = set()
    dates: set[str] = set()
    for t in texts:
        for d in DATE.findall(t):
            dates.add(d)
        for m in re.findall(r"T(\d{2}):(\d{2})", t):
            times.add(f"{m[0]}:{m[1]}")
            times.add(f"{int(m[0])}:{m[1]}")
        if re.fullmatch(r"[-+]?\d+(\.\d+)?", t.strip()):
            numbers.add(abs(float(t)))
    return numbers, times, dates


def _matches(text_number: str, allowed: set[float]) -> bool:
    raw = text_number.replace("−", "-").replace(",", "").lstrip("+-")
    value = float(raw)
    if value.is_integer() and (value <= SMALL_INT or int(value) in ALWAYS):
        return True
    places = len(raw.split(".")[1]) if "." in raw else 0
    return any(abs(round(a, places) - value) < 1e-9 for a in allowed)


def check(report: Any, facts: dict[str, Any], previous: list[dict[str, Any]], available_charts: set[str]) -> list[str]:
    errors: list[str] = []
    if not isinstance(report, dict):
        return ["the answer is not a JSON object"]
    title, summary, sections = report.get("title"), report.get("summary"), report.get("sections")
    charts, tags = report.get("charts") or [], report.get("tags") or []
    if not isinstance(title, str) or not title.strip() or len(title) > 140:
        errors.append("title: missing or longer than 140 characters")
    if not isinstance(summary, list) or len(summary) != 3 or not all(isinstance(s, str) and s.strip() for s in summary):
        errors.append("summary: exactly 3 non-empty strings are required")
    if not isinstance(sections, list) or not sections:
        errors.append("sections: at least one section is required")
        sections = []
    for s in sections:
        if not isinstance(s, dict) or s.get("id") not in SECTIONS or not isinstance(s.get("body"), str):
            errors.append(f"section {s.get('id') if isinstance(s, dict) else s!r}: unknown id or missing body "
                          f"(allowed: {', '.join(SECTIONS)})")
    bodies = [s.get("body", "") for s in sections if isinstance(s, dict) and isinstance(s.get("body"), str)]
    headings = [s.get("heading", "") for s in sections if isinstance(s, dict) and isinstance(s.get("heading"), str)]
    text_parts = ([title] if isinstance(title, str) else []) + (summary if isinstance(summary, list) else []) \
        + headings + bodies + [c.get("caption", "") for c in charts if isinstance(c, dict)]
    words = sum(len(CHART_MARK.sub("", b).split()) for b in bodies)
    if not MIN_WORDS <= words <= MAX_WORDS:
        errors.append(f"length: {words} words in the sections, expected {MIN_WORDS} to {MAX_WORDS}")
    # charts: catalogue ids, each marker has a caption and each caption a marker
    marked = [m for b in bodies for m in CHART_MARK.findall(b)]
    listed = [c.get("id") for c in charts if isinstance(c, dict)]
    if not MIN_CHARTS <= len(set(marked)) <= MAX_CHARTS:
        errors.append(f"charts: {len(set(marked))} placed, expected {MIN_CHARTS} to {MAX_CHARTS}")
    for cid in set(marked) | set(listed):
        if cid not in available_charts:
            errors.append(f"chart {cid}: not in the catalogue ({', '.join(sorted(available_charts))})")
    if set(marked) != set(listed):
        errors.append("charts: every {{chart:id}} marker needs exactly one entry in `charts` with a caption, and vice versa")
    for c in charts:
        if isinstance(c, dict) and not (isinstance(c.get("caption"), str) and c["caption"].strip()):
            errors.append(f"chart {c.get('id')}: caption missing")
    # tags
    if not isinstance(tags, list) or len(tags) > MAX_MODEL_TAGS or any(t not in TAGS for t in tags):
        errors.append(f"tags: at most {MAX_MODEL_TAGS}, from: {', '.join(TAGS)}")
    # numbers, times and dates
    numbers, times, dates = allowed_values(facts, previous)
    for part in text_parts:
        clean = CHART_MARK.sub("", part)
        for d in DATE.findall(clean):
            if d not in dates:
                errors.append(f"date {d} is not in the facts")
        clean = DATE.sub("", clean)
        for t in TIME.findall(clean):
            if t not in times:
                errors.append(f"time {t} is not in the facts")
        clean = TIME.sub("", clean)
        clean = re.sub(r"\b[A-Z0-9]{2,12}/USD\b", "", clean)          # pair symbols such as 1INCH/USD
        for n in NUMBER.findall(clean):
            if not _matches(n, numbers):
                errors.append(f"number {n} is not in the facts (copy numbers exactly, never compute new ones)")
    # tone
    joined = "\n".join(text_parts)
    for rx, label in FORBIDDEN:
        hit = rx.search(joined)
        if hit is not None:
            errors.append(f"forbidden wording ({label}): {hit.group(0)!r}")
    return sorted(set(errors), key=errors.index)
