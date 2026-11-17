"""Entry point. One tick: pull new tweets, classify, act, fire due reminders."""
from __future__ import annotations

import json
import os
import sys
from datetime import datetime, timezone
from pathlib import Path

import yaml

from . import classify as clf
from . import schedule as sched
from .notify import send_alarm, send_info
from .twitter import Tweet, TwitterClient, TwitterError


ROOT = Path(__file__).resolve().parent.parent
STATE_PATH = ROOT / "state.json"
SCHEDULE_PATH = ROOT / "schedule.json"
WATCHLIST_PATH = ROOT / "watchlist.yaml"
SOURCES_PATH = ROOT / "sources.yaml"


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


def _handle_scheduled(cls_result: clf.Classification, tweet: Tweet, movie_entry: dict, schedule: sched.Schedule) -> None:
    assert cls_result.booking_open_at is not None
    priority = movie_entry.get("priority", "normal")
    created = sched.add_or_update_schedule(
        schedule,
        movie=cls_result.movie,
        booking_open_at=cls_result.booking_open_at,
        source_url=tweet.url,
        priority=priority,
    )
    if not created:
        _log(f"no new reminders for {cls_result.movie} @ {cls_result.booking_open_at.isoformat()}")
        return
    body = (
        f"Advance bookings open: {_format_ist(cls_result.booking_open_at.isoformat())}\n"
        f"Via @{tweet.author_handle}: {tweet.url}\n\n"
        f"Reminders scheduled: {', '.join(r.kind for r in created)}."
    )
    send_info(f"📅 {cls_result.movie}", body)


def _handle_immediate(cls_result: clf.Classification, tweet: Tweet, schedule: sched.Schedule, now: datetime) -> None:
    if not sched.should_fire_immediate(schedule, cls_result.movie, now):
        _log(f"immediate already fired today for {cls_result.movie}")
        return
    body = (
        f"BOOKINGS APPEAR TO BE LIVE NOW.\n"
        f"Tweet: {tweet.text[:280]}\n"
        f"Source: @{tweet.author_handle} - {tweet.url}\n\n"
        f"Open BookMyShow / District and book NOW for PCX."
    )
    send_alarm(f"🚨 {cls_result.movie}", body)


def _fire_due_reminders(schedule: sched.Schedule, now: datetime) -> None:
    due = sched.due_reminders(schedule, now)
    for r in due:
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


def run_once() -> int:
    now = datetime.now(timezone.utc)

    watchlist_doc = yaml.safe_load(WATCHLIST_PATH.read_text())
    sources_doc = yaml.safe_load(SOURCES_PATH.read_text())
    movies = watchlist_doc.get("movies") or []
    sources = sources_doc.get("sources") or []
    if not movies:
        _log("no movies in watchlist; nothing to do")
        return 0
    if not sources:
        _log("no sources configured")
        return 0

    state = _load_state()
    schedule = sched.load(SCHEDULE_PATH)
    movies_by_name = {m["name"]: m for m in movies}

    try:
        tw = TwitterClient()
    except TwitterError as e:
        _log(f"twitter init failed: {e}")
        # still fire any due reminders
        _fire_due_reminders(schedule, now)
        sched.save(SCHEDULE_PATH, schedule)
        return 1

    with tw:
        for src in sources:
            handle = src["handle"]
            since_id = state.get(handle)
            try:
                tweets = tw.fetch_new_tweets(handle, since_id=since_id)
            except TwitterError as e:
                _log(f"skip {handle}: {e}")
                continue
            if not tweets:
                continue
            _log(f"{handle}: {len(tweets)} new tweet(s)")
            for tw_obj in tweets:
                cls_result = clf.classify(tw_obj.text, tw_obj.created_at, movies)
                if cls_result.verdict == "IGNORE":
                    continue
                movie_entry = movies_by_name.get(cls_result.movie)
                if not movie_entry:
                    continue
                if cls_result.verdict == "SCHEDULED":
                    _handle_scheduled(cls_result, tw_obj, movie_entry, schedule)
                elif cls_result.verdict == "IMMEDIATE":
                    _handle_immediate(cls_result, tw_obj, schedule, now)
            state[handle] = tweets[-1].id

    _fire_due_reminders(schedule, now)

    _save_state(state)
    sched.save(SCHEDULE_PATH, schedule)
    return 0


if __name__ == "__main__":
    sys.exit(run_once())
