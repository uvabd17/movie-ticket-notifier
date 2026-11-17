"""Google News RSS reader. Free, legal, no API key, no rate limits that matter
for our 5-min cron. Per-movie queries return articles from news sites that
report on ticket-booking openings.

News articles published within the last 24h that say "advance booking opens"
(present tense) are treated as IMMEDIATE — the article is reporting that
bookings have just opened. Articles with a future date are SCHEDULED.
"""
from __future__ import annotations

import re
import urllib.parse
import urllib.request
from dataclasses import dataclass
from datetime import datetime, timezone
from xml.etree import ElementTree as ET


RSS_BASE = "https://news.google.com/rss/search"
USER_AGENT = "Mozilla/5.0 (movie-ticket-notifier)"


@dataclass
class NewsItem:
    guid: str                # stable id (article url or guid)
    title: str
    description: str
    published_at: datetime   # tz-aware UTC
    source: str              # e.g. "Sacnilk"
    link: str                # Google News redirect URL


def default_query(movie_name: str) -> str:
    """Reasonable default for a movie we don't have a custom query for."""
    return f'"{movie_name}" ("advance booking" OR "bookings open" OR "tickets go live" OR "booking opens")'


def fetch(query: str, *, locale: str = "en-IN", country: str = "IN") -> list[NewsItem]:
    url = (
        f"{RSS_BASE}?q={urllib.parse.quote(query)}"
        f"&hl={locale}&gl={country}&ceid={country}:{locale.split('-')[0]}"
    )
    req = urllib.request.Request(url, headers={"User-Agent": USER_AGENT})
    with urllib.request.urlopen(req, timeout=15) as r:
        body = r.read()
    root = ET.fromstring(body)
    items = []
    for it in root.findall(".//item"):
        title = (it.findtext("title") or "").strip()
        desc = (it.findtext("description") or "").strip()
        link = (it.findtext("link") or "").strip()
        guid = (it.findtext("guid") or link).strip()
        pub_raw = (it.findtext("pubDate") or "").strip()
        src_el = it.find("source")
        source = (src_el.text or "").strip() if src_el is not None else ""
        # Google News appends " - Source" to titles; strip it
        title = re.sub(r"\s+-\s+[^-]+$", "", title)
        # Description is HTML; strip tags for simple keyword matching
        desc = re.sub(r"<[^>]+>", " ", desc)
        desc = re.sub(r"\s+", " ", desc).strip()
        try:
            published_at = _parse_rfc822(pub_raw)
        except ValueError:
            continue
        items.append(
            NewsItem(
                guid=guid,
                title=title,
                description=desc,
                published_at=published_at,
                source=source,
                link=link,
            )
        )
    return items


_MONTHS = {
    "Jan": 1, "Feb": 2, "Mar": 3, "Apr": 4, "May": 5, "Jun": 6,
    "Jul": 7, "Aug": 8, "Sep": 9, "Oct": 10, "Nov": 11, "Dec": 12,
}


def _parse_rfc822(s: str) -> datetime:
    """Parse 'Tue, 17 Nov 2026 05:55:28 GMT' without pulling in email.utils."""
    m = re.match(
        r"^[A-Za-z]{3},\s+(\d{1,2})\s+([A-Za-z]{3})\s+(\d{4})\s+"
        r"(\d{2}):(\d{2}):(\d{2})\s+(GMT|UTC|\+0000|\+\d{4}|-\d{4})$",
        s,
    )
    if not m:
        raise ValueError(f"bad RFC822 date: {s!r}")
    day, mon, year, hh, mm, ss, tz = m.groups()
    month = _MONTHS.get(mon)
    if not month:
        raise ValueError(f"bad month: {mon!r}")
    dt = datetime(int(year), month, int(day), int(hh), int(mm), int(ss), tzinfo=timezone.utc)
    return dt
