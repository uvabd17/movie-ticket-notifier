"""Instagram source via xpoz Python SDK. Gated by last-check time to conserve
xpoz credits (Free plan has ~500/mo). Default poll interval 30 min — Instagram
posts trail the booking flip by a few minutes anyway, so sub-30-min polling
doesn't gain us much."""
from __future__ import annotations

import os
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Any

try:
    from xpoz import XpozClient  # type: ignore
    _XPOZ_AVAILABLE = True
except ImportError:  # pragma: no cover
    _XPOZ_AVAILABLE = False


@dataclass
class InstaPost:
    id: str
    username: str
    caption: str
    created_at: datetime   # tz-aware UTC
    url: str


class InstagramError(RuntimeError):
    pass


def available() -> bool:
    """True if we can poll Instagram this tick (SDK present and key set)."""
    return _XPOZ_AVAILABLE and bool(os.environ.get("XPOZ_API_KEY"))


def fetch_posts(username: str, limit: int = 10) -> list[InstaPost]:
    """Fetch up to `limit` recent posts from `username`. Returns oldest first."""
    if not _XPOZ_AVAILABLE:
        raise InstagramError("xpoz SDK not installed")
    key = os.environ.get("XPOZ_API_KEY")
    if not key:
        raise InstagramError("XPOZ_API_KEY not set")

    client = XpozClient(api_key=key)
    result = client.instagram.get_posts_by_user(
        identifier=username,
        identifier_type="username",
        limit=limit,
        fields=["id", "caption", "username", "createdAt", "createdAtTimestamp", "codeUrl"],
    )
    raw = _extract_items(result)
    posts: list[InstaPost] = []
    for item in raw:
        pid = _get(item, "id")
        if not pid:
            continue
        caption = _get(item, "caption") or ""
        uname = _get(item, "username") or username
        ts = _get(item, "createdAtTimestamp") or _get(item, "createdAt")
        created_at = _parse_timestamp(ts)
        code_url = _get(item, "codeUrl") or ""
        url = code_url or f"https://www.instagram.com/{uname}/"
        posts.append(InstaPost(
            id=str(pid),
            username=uname,
            caption=caption,
            created_at=created_at,
            url=url,
        ))
    posts.sort(key=lambda p: p.created_at)
    return posts


def _extract_items(result: Any) -> list[Any]:
    # xpoz SDK returns a PaginatedResult; the posts may be at `.data`, `.results`,
    # or on `.items`. Handle a few shapes defensively.
    for attr in ("data", "results", "items"):
        got = getattr(result, attr, None)
        if got:
            return list(got)
    if isinstance(result, dict):
        for k in ("data", "results", "items"):
            if result.get(k):
                return list(result[k])
    if isinstance(result, list):
        return result
    return []


def _get(obj: Any, key: str) -> Any:
    if isinstance(obj, dict):
        return obj.get(key)
    return getattr(obj, key, None)


def _parse_timestamp(ts: Any) -> datetime:
    if ts is None:
        return datetime.now(timezone.utc)
    # Unix seconds (int or stringified)
    if isinstance(ts, (int, float)) or (isinstance(ts, str) and ts.isdigit()):
        return datetime.fromtimestamp(int(ts), tz=timezone.utc)
    # ISO string
    if isinstance(ts, str):
        s = ts.replace("Z", "+00:00")
        try:
            return datetime.fromisoformat(s).astimezone(timezone.utc)
        except ValueError:
            pass
    return datetime.now(timezone.utc)
