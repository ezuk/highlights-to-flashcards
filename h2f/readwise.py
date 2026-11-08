"""Readwise: checking a token and keeping a local copy of every book's highlights.

Uses the v2 export API. The first pull fetches everything; later pulls ask only for
what changed since the last one started, including deletions.
"""

import json
import time
import urllib.error
import urllib.parse
import urllib.request
from datetime import datetime, timezone

from . import paths

TIMEOUT_SECONDS = 60
MAX_WAIT_SECONDS = 120
CATEGORY = "books"


class ReadwiseError(Exception):
    """A plain sentence for the screen."""


def _request(path, token, params=None, sleep=time.sleep):
    url = paths.readwise_url() + path
    if params:
        url += "?" + urllib.parse.urlencode(params)
    request = urllib.request.Request(url, headers={"Authorization": f"Token {token}",
                                                   "User-Agent": "Highlights to Flashcards"})
    for _attempt in range(5):
        try:
            with urllib.request.urlopen(request, timeout=TIMEOUT_SECONDS) as response:
                body = response.read()
                return response.status, (json.loads(body) if body else None)
        except urllib.error.HTTPError as e:
            if e.code == 429:
                try:
                    wait = int(e.headers.get("Retry-After", "10"))
                except ValueError:
                    wait = 10
                sleep(min(max(wait, 1), MAX_WAIT_SECONDS))
                continue
            if e.code in (401, 403):
                raise ReadwiseError("Readwise didn't accept the token.") from None
            raise ReadwiseError(f"Readwise answered with an error ({e.code}).") from None
        except (urllib.error.URLError, TimeoutError, OSError):
            raise ReadwiseError("Couldn't reach Readwise. Check the internet connection.") from None
        except ValueError:
            raise ReadwiseError("Readwise sent something that isn't readable.") from None
    raise ReadwiseError("Readwise is busy. Try again in a minute.")


def check_token(token, sleep=time.sleep):
    """True when Readwise accepts the token."""
    try:
        status, _body = _request("/api/v2/auth/", token, sleep=sleep)
    except ReadwiseError as e:
        if "didn't accept" in str(e):
            return False
        raise
    return status == 204


def _now_iso():
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def _book_row(item):
    return {
        "id": item["user_book_id"],
        "title": item.get("title") or item.get("readable_title") or "",
        "author": item.get("author") or "",
        "category": item.get("category") or "",
        "source": item.get("source") or "",
    }


def _highlight_row(item, book_id):
    return {
        "id": item["id"],
        "book_id": book_id,
        "text": item.get("text") or "",
        "note": item.get("note") or "",
        "location": item.get("location"),
        "location_type": item.get("location_type") or "",
        "highlighted_at": item.get("highlighted_at") or item.get("created_at") or "",
        "updated_at": item.get("updated_at") or "",
    }


def pull(store, token, on_page=None, sleep=time.sleep):
    """Brings the local copy up to date. Returns {"books": n, "highlights": n, "removed": n}."""
    started = _now_iso()
    since = store.get_setting("readwise_pulled_at")
    params = {"includeDeleted": "true"} if since else {}
    if since:
        params["updatedAfter"] = since
    counts = {"books": 0, "highlights": 0, "removed": 0}
    cursor = None
    while True:
        page = dict(params, pageCursor=cursor) if cursor else params
        _status, body = _request("/api/v2/export/", token, page or None, sleep=sleep)
        if not isinstance(body, dict) or not isinstance(body.get("results"), list):
            raise ReadwiseError("Readwise sent something that isn't readable.")
        for item in body["results"]:
            if not isinstance(item, dict) or "user_book_id" not in item:
                continue
            book = _book_row(item)
            if item.get("is_deleted"):
                store.remove_readwise_book(book["id"])
                continue
            if book["category"] != CATEGORY:
                continue
            store.upsert_readwise_book(book)
            counts["books"] += 1
            for h in item.get("highlights") or []:
                if not isinstance(h, dict) or "id" not in h:
                    continue
                if h.get("is_deleted") or h.get("is_discard"):
                    counts["removed"] += store.remove_readwise_highlight(h["id"])
                    continue
                store.upsert_readwise_highlight(_highlight_row(h, book["id"]))
                counts["highlights"] += 1
        if on_page:
            on_page(counts)
        cursor = body.get("nextPageCursor")
        if not cursor:
            break
    store.set_setting("readwise_pulled_at", started)
    return counts
