"""Twitter/X API v2 client. Fetches new tweets per handle using since_id."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone

import httpx


API_BASE = "https://api.twitter.com/2"


@dataclass
class Tweet:
    id: str
    author_handle: str
    text: str
    created_at: datetime
    url: str


class TwitterError(RuntimeError):
    pass


class TwitterClient:
    def __init__(self, bearer_token: str | None = None):
        token = bearer_token or os.environ.get("TWITTER_BEARER_TOKEN")
        if not token:
            raise TwitterError("TWITTER_BEARER_TOKEN not set")
        self._client = httpx.Client(
            headers={"Authorization": f"Bearer {token}"},
            timeout=15.0,
        )
        self._user_id_cache: dict[str, str] = {}

    def close(self) -> None:
        self._client.close()

    def __enter__(self):
        return self

    def __exit__(self, *_):
        self.close()

    def _user_id(self, handle: str) -> str:
        if handle in self._user_id_cache:
            return self._user_id_cache[handle]
        r = self._client.get(f"{API_BASE}/users/by/username/{handle}")
        if r.status_code != 200:
            raise TwitterError(f"user lookup {handle}: {r.status_code} {r.text}")
        data = r.json().get("data")
        if not data:
            raise TwitterError(f"user not found: {handle}")
        self._user_id_cache[handle] = data["id"]
        return data["id"]

    def fetch_new_tweets(
        self,
        handle: str,
        since_id: str | None,
        max_results: int = 20,
    ) -> list[Tweet]:
        """Fetch tweets from `handle` newer than `since_id`. Oldest first."""
        user_id = self._user_id(handle)
        params = {
            "max_results": max(5, min(max_results, 100)),
            "tweet.fields": "created_at,author_id",
            "exclude": "retweets,replies",
        }
        if since_id:
            params["since_id"] = since_id
        r = self._client.get(f"{API_BASE}/users/{user_id}/tweets", params=params)
        if r.status_code == 429:
            raise TwitterError(f"rate limited on {handle}")
        if r.status_code != 200:
            raise TwitterError(f"tweets {handle}: {r.status_code} {r.text}")
        payload = r.json()
        raw = payload.get("data") or []
        tweets = [
            Tweet(
                id=t["id"],
                author_handle=handle,
                text=t["text"],
                created_at=_parse_dt(t["created_at"]),
                url=f"https://x.com/{handle}/status/{t['id']}",
            )
            for t in raw
        ]
        tweets.sort(key=lambda x: x.id)
        return tweets


def _parse_dt(s: str) -> datetime:
    # Twitter returns RFC3339: "2026-11-17T06:30:00.000Z"
    if s.endswith("Z"):
        s = s[:-1] + "+00:00"
    return datetime.fromisoformat(s).astimezone(timezone.utc)
