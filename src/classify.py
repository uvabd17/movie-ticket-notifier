"""Classifier: given a tweet + watchlist, return SCHEDULED / IMMEDIATE / IGNORE.

Phase 1: regex only. Absolute dates work ("Nov 17 at 12 noon"), relative language
("tomorrow", "this Friday") does not and returns IGNORE; those are picked up in
Phase 2 by the LLM date extractor.
"""
from __future__ import annotations

import re
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from typing import Literal

from dateutil import parser as dateparser


Verdict = Literal["SCHEDULED", "IMMEDIATE", "IGNORE"]

IST = timezone(timedelta(hours=5, minutes=30))


@dataclass
class Classification:
    verdict: Verdict
    movie: str | None = None
    booking_open_at: datetime | None = None  # tz-aware IST
    reason: str = ""


# --- phrase sets ---------------------------------------------------------

# Present-tense: "bookings are live now". Must NOT also contain a future date.
# "book your tickets soon" is intentionally excluded (forward-looking promo).
IMMEDIATE_RE = re.compile(
    r"\b("
    r"bookings?\s+(are\s+)?(now\s+)?(live|open|out)\s+now"
    r"|bookings?\s+(are\s+)?now\s+(live|open|out)"
    r"|tickets?\s+(are\s+)?(now\s+)?(live|out|available)\s+now"
    r"|tickets?\s+(are\s+)?now\s+(live|open|out|available)"
    r"|book\s+(now|tickets?\s+now|here)"
    r"|on\s+sale\s+now"
    r"|live\s+now\s+on"
    r")\b",
    re.IGNORECASE,
)

# Future-tense: "bookings open <date>", "advance bookings for X open on <date>".
# Uses a short lazy gap to allow a movie name / hashtag / "for IMAX" between the
# booking-noun and the verb. Capped at 80 chars so we don't match across tweets.
_GAP = r"[^.!?\n]{0,80}?"
SCHEDULED_RE = re.compile(
    r"\b("
    rf"advance\s+bookings?{_GAP}\s+(opens?|starts?|begins?|goes?\s+live|from)"
    rf"|bookings?{_GAP}\s+(open|start|begin|go\s+live)\s+(on|from|at)"
    rf"|tickets?{_GAP}\s+(go\s+live|on\s+sale|open|out)\s+(on|from)"
    rf"|pre[-\s]?book(?:ing)?s?{_GAP}\s+(opens?|starts?|from|on)"
    r")\b",
    re.IGNORECASE,
)

# If any of these appear, kill the verdict (past tense, cancellations, etc.)
NEGATIVE_RE = re.compile(
    r"\b("
    r"opened|closed|sold\s*out|postponed|cancell?ed|delayed"
    r"|coming\s+soon"
    r"|notify\s+me"
    r"|advance\s+booking\s+will\s+(open|start)"
    r")\b",
    re.IGNORECASE,
)

# Links that confirm an IMMEDIATE tweet points to a real booking page
BOOKING_URL_RE = re.compile(
    r"(https?://)?(in\.)?(bookmyshow\.com|district\.in|bit\.ly|t\.co|tinyurl\.com)/\S+",
    re.IGNORECASE,
)

# Absolute date patterns. Captures one match string; dateutil parses the rest.
# Examples matched:
#   "Nov 17", "17 Nov", "November 17", "Nov 17, 2026", "17/11/2026", "17-11-26"
#   with optional "at 12 noon" / "at 10:30 AM" / "12:00 IST"
_MONTH = r"(?:Jan|Feb|Mar|Apr|May|Jun|Jul|Aug|Sep|Sept|Oct|Nov|Dec)[a-z]*"
_DAY = r"(?:[0-3]?\d)"
_YEAR = r"(?:\d{2}|\d{4})"
DATE_RE = re.compile(
    rf"(?P<date>"
    rf"{_MONTH}\.?\s+{_DAY}(?:,?\s+{_YEAR})?"
    rf"|{_DAY}(?:st|nd|rd|th)?\s+{_MONTH}(?:,?\s+{_YEAR})?"
    rf"|{_DAY}[/-]{_DAY}[/-]{_YEAR}"
    rf")",
    re.IGNORECASE,
)

TIME_RE = re.compile(
    r"\bat\s+(?P<time>"
    r"\d{1,2}(:\d{2})?\s*(am|pm|noon|midnight|AM|PM)"
    r"|\d{1,2}:\d{2}\s*(IST|hrs)?"
    r")",
    re.IGNORECASE,
)

NOON_RE = re.compile(r"\b12\s*noon\b|\bnoon\b", re.IGNORECASE)
MIDNIGHT_RE = re.compile(r"\bmidnight\b", re.IGNORECASE)


# --- movie matching ------------------------------------------------------

def _tokens(s: str) -> set[str]:
    return set(re.findall(r"[a-z0-9]+", s.lower()))


def match_movie(tweet_text: str, watchlist: list[dict]) -> str | None:
    text = tweet_text.lower()
    text_tokens = _tokens(tweet_text)
    for movie in watchlist:
        name = movie["name"]
        candidates = [name] + list(movie.get("aliases") or [])
        for cand in candidates:
            cand_tokens = _tokens(cand)
            if cand_tokens and cand_tokens.issubset(text_tokens):
                return name
        for tag in movie.get("hashtags") or []:
            if f"#{tag.lower()}" in text:
                return name
    return None


# --- date extraction -----------------------------------------------------

def extract_datetime(text: str, reference: datetime) -> datetime | None:
    """Extract an absolute future datetime from `text`. Returns None if ambiguous.

    `reference` is the tweet's creation time, used to default the year.
    Returned datetime is tz-aware in IST.
    """
    m = DATE_RE.search(text)
    if not m:
        return None
    date_str = m.group("date")

    # Prefer explicit noon/midnight tokens over a raw time match, because
    # dateutil can't parse "12 noon" even though humans and TIME_RE both can.
    time_str: str | None = None
    if NOON_RE.search(text):
        time_str = "12:00 PM"
    elif MIDNIGHT_RE.search(text):
        time_str = "12:00 AM"
    else:
        tm = TIME_RE.search(text)
        if tm:
            time_str = tm.group("time")

    composed = f"{date_str} {time_str}" if time_str else date_str
    try:
        ref_ist = reference.astimezone(IST)
        parsed = dateparser.parse(composed, default=ref_ist.replace(hour=12, minute=0, second=0, microsecond=0))
    except (ValueError, OverflowError):
        return None

    if parsed.tzinfo is None:
        parsed = parsed.replace(tzinfo=IST)
    else:
        parsed = parsed.astimezone(IST)

    # If year defaulted to the past, roll forward one year
    if parsed < ref_ist - timedelta(hours=12):
        parsed = parsed.replace(year=parsed.year + 1)

    # Only accept dates within the next 90 days to avoid wild misparses
    if parsed > ref_ist + timedelta(days=90):
        return None
    if parsed < ref_ist - timedelta(hours=6):
        return None

    return parsed


# --- main --------------------------------------------------------------

def classify(tweet_text: str, tweet_time: datetime, watchlist: list[dict]) -> Classification:
    if NEGATIVE_RE.search(tweet_text):
        return Classification("IGNORE", reason="negative phrase")

    movie = match_movie(tweet_text, watchlist)
    if not movie:
        return Classification("IGNORE", reason="no movie match")

    sched_hit = SCHEDULED_RE.search(tweet_text)
    imm_hit = IMMEDIATE_RE.search(tweet_text)

    if sched_hit:
        when = extract_datetime(tweet_text, tweet_time)
        if when is None:
            return Classification(
                "IGNORE",
                movie=movie,
                reason="scheduled phrasing but no parseable absolute date (Phase 2 LLM territory)",
            )
        return Classification("SCHEDULED", movie=movie, booking_open_at=when, reason="scheduled hit with date")

    if imm_hit:
        if not BOOKING_URL_RE.search(tweet_text):
            return Classification("IGNORE", movie=movie, reason="immediate phrasing but no booking URL")
        return Classification("IMMEDIATE", movie=movie, reason="immediate hit with URL")

    return Classification("IGNORE", movie=movie, reason="no booking phrasing")
