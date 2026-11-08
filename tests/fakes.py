"""Stand-ins for Readwise, a Calibre library and AnkiWeb, built from public-domain books only."""

import json
import shutil
import sqlite3
import threading
import urllib.parse
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

FIXTURES = Path(__file__).resolve().parent / "fixtures"
PRIDE_EPUB = FIXTURES / "books" / "pride-and-prejudice.epub"
TOKEN = "test-token"

PRIDE_ID = 101
MEDITATIONS_ID = 102
ARTICLE_ID = 103


def _highlight(hid, text, page, when, note=""):
    return {"id": hid, "text": text, "note": note, "location": page, "location_type": "order",
            "highlighted_at": when, "updated_at": when, "is_deleted": False}


def export_items():
    """Readwise export results: two books and an article, which the app leaves out."""
    return [
        {
            "user_book_id": PRIDE_ID, "title": "Pride and Prejudice", "author": "Jane Austen",
            "category": "books", "source": "koreader", "is_deleted": False,
            "highlights": [
                _highlight(1001, "It is a truth universally acknowledged, that a single man in possession of a good "
                                 "fortune must be in want of a wife.", 1, "2026-01-02T10:00:00Z"),
                _highlight(1002, "Do let the portraits of your uncle and aunt Philips be placed in the gallery at "
                                 "Pemberley.", 40, "2026-01-03T10:00:00Z", note="Elizabeth teasing Darcy"),
                _highlight(1003, "As her successor in that house, she regarded her with jealous abhorrence.", 120,
                           "2026-01-04T10:00:00Z"),
            ],
        },
        {
            "user_book_id": MEDITATIONS_ID, "title": "Meditations", "author": "Marcus Aurelius",
            "category": "books", "source": "koreader", "is_deleted": False,
            "highlights": [
                _highlight(2001, "You have power over your mind, not outside events.", 12, "2026-01-01T10:00:00Z"),
            ],
        },
        {
            "user_book_id": ARTICLE_ID, "title": "An article", "author": "Someone",
            "category": "articles", "source": "reader", "is_deleted": False,
            "highlights": [_highlight(3001, "Not a book.", None, "2026-01-01T10:00:00Z")],
        },
    ]


class FakeReadwise:
    """The two Readwise endpoints the app uses, one book per page. `items` can be changed between pulls."""

    def __init__(self):
        self.items = export_items()
        self.requests = []
        fake = self

        class Handler(BaseHTTPRequestHandler):
            def log_message(self, *_args):
                pass

            def _send(self, status, body=None):
                data = json.dumps(body).encode() if body is not None else b""
                self.send_response(status)
                self.send_header("Content-Type", "application/json")
                self.send_header("Content-Length", str(len(data)))
                self.end_headers()
                self.wfile.write(data)

            def do_GET(self):
                url = urllib.parse.urlsplit(self.path)
                query = dict(urllib.parse.parse_qsl(url.query))
                fake.requests.append((url.path, query))
                if self.headers.get("Authorization") != f"Token {TOKEN}":
                    return self._send(401, {"detail": "Invalid token."})
                if url.path == "/api/v2/auth/":
                    return self._send(204)
                if url.path == "/api/v2/export/":
                    return self._send(200, fake.page(query))
                return self._send(404, {"detail": "Not found."})

        self.http = ThreadingHTTPServer(("127.0.0.1", 0), Handler)
        self.url = f"http://127.0.0.1:{self.http.server_address[1]}"
        self.thread = threading.Thread(target=self.http.serve_forever, daemon=True)

    def page(self, query):
        since = query.get("updatedAfter")
        items = []
        for item in self.items:
            highlights = [h for h in item["highlights"] if not since or h["updated_at"] > since]
            if since and not highlights and not item.get("is_deleted"):
                continue
            if not since:
                highlights = [h for h in highlights if not h.get("is_deleted")]
            items.append({**item, "highlights": highlights})
        at = int(query.get("pageCursor", 0))
        return {"count": len(items), "nextPageCursor": str(at + 1) if at + 1 < len(items) else None,
                "results": items[at:at + 1]}

    def __enter__(self):
        self.thread.start()
        return self

    def __exit__(self, *_exc):
        self.http.shutdown()
        self.http.server_close()


def make_calibre_library(folder):
    """A Calibre library with Pride and Prejudice and a book with no ePub. Returns its path."""
    folder = Path(folder)
    book_dir = folder / "Jane Austen" / "Pride and Prejudice (1)"
    book_dir.mkdir(parents=True, exist_ok=True)
    shutil.copy(PRIDE_EPUB, book_dir / "Pride and Prejudice - Jane Austen.epub")
    (folder / "Lewis Carroll" / "Alice (2)").mkdir(parents=True, exist_ok=True)
    conn = sqlite3.connect(folder / "metadata.db")
    conn.executescript("""
        CREATE TABLE books (id INTEGER PRIMARY KEY, title TEXT, path TEXT);
        CREATE TABLE authors (id INTEGER PRIMARY KEY, name TEXT);
        CREATE TABLE books_authors_link (id INTEGER PRIMARY KEY, book INTEGER, author INTEGER);
        CREATE TABLE data (id INTEGER PRIMARY KEY, book INTEGER, format TEXT, name TEXT);
        INSERT INTO books VALUES (1, 'Pride and Prejudice', 'Jane Austen/Pride and Prejudice (1)');
        INSERT INTO books VALUES (2, 'Alice''s Adventures in Wonderland', 'Lewis Carroll/Alice (2)');
        INSERT INTO authors VALUES (1, 'Jane Austen'), (2, 'Lewis Carroll');
        INSERT INTO books_authors_link VALUES (1, 1, 1), (2, 2, 2);
        INSERT INTO data VALUES (1, 1, 'EPUB', 'Pride and Prejudice - Jane Austen');
        INSERT INTO data VALUES (2, 2, 'MOBI', 'Alice');
    """)
    conn.commit()
    conn.close()
    return str(folder)


def fake_ankiweb(collection_class):
    """Swaps AnkiWeb for a stand-in: any email and password log in, and a sync marks every local
    change as sent. Returns a function that puts the real methods back."""
    from h2f import keychain

    real = {name: getattr(collection_class, name) for name in ("login", "sync")}

    def login(self, email, password):
        if password != "test-password":
            from h2f.collection import CollectionError
            raise CollectionError("AnkiWeb didn't accept that email and password.")
        keychain.put(keychain.ANKIWEB, {"hkey": "test-hkey", "endpoint": ""})

    def sync(self):
        from h2f.collection import CollectionError, NOT_LOGGED_IN
        if not self.logged_in():
            raise CollectionError(NOT_LOGGED_IN)
        with self.lock:
            db = self._open().db
            for table in ("notes", "cards", "revlog", "graves"):
                db.execute(f"UPDATE {table} SET usn = 0 WHERE usn = -1")
        return "synced"

    collection_class.login = login
    collection_class.sync = sync

    def restore():
        for name, method in real.items():
            setattr(collection_class, name, method)

    return restore
