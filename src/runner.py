"""Entry point. One tick: pull news (primary) + twitter (optional),
classify, act, fire due reminders."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import classify as clf
from . import newsrss
from . import schedule as sched
from .notify import send_alarm, send_info
from .twitter import Tweet, TwitterClient, TwitterError


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state.json"
SCHEDULE_PATH = ROOT / "schedule.json"
WATCHLIST_PATH = ROOT / "watchlist.yaml"
SOURCES_PATH = ROOT / "sources.yaml"

# Bound per-query guid history to avoid state.json growing unbounded.
MAX_GUIDS_PER_QUERY = 500


def _log(msg: str) -> None:
    print(f"[runner] {msg}")


def _load_state() -> dict:
    if STATE_PATH.exists():
        return json.loads(STATE_PATH.read_text())
    return {}


def _save_state(state: dict) -> None:
    STATE_PATH.write_text(json.dumps(state, indent=2, sort_keys=True))


def _format_ist(iso: str) -> str:
    dt = datetime.fromisoformat(iso)
    return dt.strftime("%a %d %b, %I:%M %p IST")


# --- action handlers ---------------------------------------------------

def _handle_scheduled(
    cls_result: clf.Classification,
    source_label: str,
    source_url: str,
    movie_entry: dict,
    schedule: sched.Schedule,
) -> None:
    assert cls_result.booking_open_at is not None
    priority = movie_entry.get("priority", "normal")
    created = sched.add_or_update_schedule(
        schedule,
        movie=cls_result.movie,
        booking_open_at=cls_result.booking_open_at,
        source_url=source_url,
        priority=priority,
    )
    if not created:
        _log(f"no new reminders for {cls_result.movie} @ {cls_result.booking_open_at.isoformat()}")
        return
    body = (
        f"Advance bookings open: {_format_ist(cls_result.booking_open_at.isoformat())}\n"
        f"Via {source_label}: {source_url}\n\n"
        f"Reminders scheduled: {', '.join(r.kind for r in created)}."
    )
    send_info(f"📅 {cls_result.movie}", body)


def _handle_immediate(
    cls_result: clf.Classification,
    source_label: str,
    source_text: str,
    source_url: str,
    schedule: sched.Schedule,
    now: datetime,
) -> None:
    if not sched.should_fire_immediate(schedule, cls_result.movie, now):
        _log(f"immediate already fired today for {cls_result.movie}")
        return
    body = (
        f"BOOKINGS APPEAR TO BE LIVE NOW.\n"
        f'"{source_text[:240]}"\n'
        f"Source: {source_label} — {source_url}\n\n"
        f"Open BookMyShow / District and book NOW for PCX."
    )
    send_alarm(f"🚨 {cls_result.movie}", body)


def _fire_due_reminders(schedule: sched.Schedule, now: datetime) -> None:
    for r in sched.due_reminders(schedule, now):
        booking_at_human = _format_ist(r.booking_open_at)
        if r.kind == "T-5min":
            title = f"🚨 {r.movie} — BOOKINGS IN 5 MINUTES"
            body = (
                f"Open BMS / District NOW for PCX.\n"
                f"Opens at: {booking_at_human}\n"
                f"Source: {r.source_url}"
            )
            send_alarm(title, body)
        elif r.kind == "T+0":
            title = f"🚨 {r.movie} — BOOKINGS OPEN NOW"
            body = f"Refresh BMS / District.\nOpens at: {booking_at_human}"
            send_alarm(title, body)
        elif r.kind == "T+2min":
            title = f"{r.movie} — 2 min post-open check"
            body = "If you haven't booked PCX yet, good seats may still be there — go now."
            send_info(title, body)
        elif r.kind == "T-24h":
            title = f"📅 {r.movie} — bookings tomorrow"
            body = f"Opens: {booking_at_human}\nSource: {r.source_url}"
            send_info(title, body)
        elif r.kind == "T-1h":
            title = f"⏰ {r.movie} — bookings in 1 hour"
            body = f"Opens at: {booking_at_human}\nBe near your phone.\nSource: {r.source_url}"
            send_info(title, body)
        sched.mark_sent(schedule, r, now)


# --- news source -------------------------------------------------------

def _news_state_key(movie_name: str, query: str) -> str:
    # Keep key readable but unique per (movie, query) pair
    return f"news:{movie_name}::{query}"


def _run_news(state: dict, movies: list[dict], schedule: sched.Schedule, now: datetime) -> None:
    for movie in movies:
        name = movie["name"]
        queries = movie.get("google_news_queries") or [newsrss.default_query(name)]
        for query in queries:
            key = _news_state_key(name, query)
            seen = set(state.get(key, []))
            try:
                items = newsrss.fetch(query)
            except Exception as e:
                _log(f"news fetch failed [{name} :: {query}]: {e}")
                continue
            new_items = [it for it in items if it.guid not in seen]
            if not new_items:
                continue
            _log(f"news [{name}]: {len(new_items)} new article(s)")
            for it in new_items:
                text = f"{it.title}. {it.description}"
                cls = clf.classify(text, it.published_at, movies, source_type="news")
                if cls.verdict == "IGNORE":
                    continue
                if cls.movie != name:
                    # Classifier matched a different watchlisted movie (shouldn't
                    # happen here but guard anyway).
                    continue
                label = f"news:{it.source or '?'}"
                if cls.verdict == "SCHEDULED":
                    _handle_scheduled(cls, label, it.link, movie, schedule)
                else:  # IMMEDIATE
                    _handle_immediate(cls, label, it.title, it.link, schedule, now)
            # Update seen set with all items we just fetched (bounded)
            seen.update(it.guid for it in items)
            trimmed = list(seen)
            if len(trimmed) > MAX_GUIDS_PER_QUERY:
                trimmed = trimmed[-MAX_GUIDS_PER_QUERY:]
            state[key] = trimmed


# --- twitter source (optional) -----------------------------------------

def _run_twitter(state: dict, movies: list[dict], sources: list[dict], schedule: sched.Schedule, now: datetime) -> None:
    if not os.environ.get("TWITTER_BEARER_TOKEN"):
        _log("twitter disabled: TWITTER_BEARER_TOKEN not set (expected on free plan)")
        return
    try:
        tw = TwitterClient()
    except TwitterError as e:
        _log(f"twitter init failed: {e}")
        return
    with tw:
        for src in sources:
            handle = src["handle"]
            key = f"twitter:{handle}"
            since_id = state.get(key)
            try:
                tweets = tw.fetch_new_tweets(handle, since_id=since_id)
            except TwitterError as e:
                _log(f"twitter skip {handle}: {e}")
                continue
            if not tweets:
                continue
            _log(f"twitter [{handle}]: {len(tweets)} new tweet(s)")
            for tw_obj in tweets:
                cls = clf.classify(tw_obj.text, tw_obj.created_at, movies, source_type="tweet")
                if cls.verdict == "IGNORE":
                    continue
                movie_entry = next((m for m in movies if m["name"] == cls.movie), None)
                if not movie_entry:
                    continue
                label = f"tweet:@{handle}"
                if cls.verdict == "SCHEDULED":
                    _handle_scheduled(cls, label, tw_obj.url, movie_entry, schedule)
                else:
                    _handle_immediate(cls, label, tw_obj.text, tw_obj.url, schedule, now)
            state[key] = tweets[-1].id


# --- entry -------------------------------------------------------------

def run_once() -> int:
    now = datetime.now(timezone.utc)

    watchlist_doc = yaml.safe_load(WATCHLIST_PATH.read_text())
    sources_doc = yaml.safe_load(SOURCES_PATH.read_text()) if SOURCES_PATH.exists() else {}
    movies = watchlist_doc.get("movies") or []
    sources = (sources_doc or {}).get("sources") or []
    if not movies:
        _log("no movies in watchlist; nothing to do")
        return 0

    state = _load_state()
    schedule = sched.load(SCHEDULE_PATH)

    _run_news(state, movies, schedule, now)
    _run_twitter(state, movies, sources, schedule, now)
    _fire_due_reminders(schedule, now)

    _save_state(state)
    sched.save(SCHEDULE_PATH, schedule)
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
