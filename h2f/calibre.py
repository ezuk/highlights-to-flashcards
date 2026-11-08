"""Calibre: finding a Readwise book's ePub and Book Companion in the Calibre library.

Reads the library's `metadata.db` read-only, which works while Calibre is open.
"""

import json
import os
import re
import sqlite3
import unicodedata
from pathlib import Path

GLOBAL_PREFS = Path.home() / "Library" / "Preferences" / "calibre" / "global.py.json"


class CalibreError(Exception):
    """A plain sentence for the screen."""


def default_library():
    """The library Calibre last used, or None."""
    override = os.environ.get("H2F_CALIBRE_LIBRARY")
    if override:
        return override
    try:
        path = json.loads(GLOBAL_PREFS.read_text(encoding="utf-8")).get("library_path")
    except (OSError, ValueError):
        return None
    return path if isinstance(path, str) and path else None


def list_books(library):
    """[{id, title, authors, epub, companion}] for every book in the library."""
    if not library:
        raise CalibreError("No Calibre library is set. Pick one in Settings.")
    db = Path(library) / "metadata.db"
    if not db.is_file():
        raise CalibreError("That folder isn't a Calibre library.")
    try:
        conn = sqlite3.connect(f"{db.as_uri()}?mode=ro", uri=True, timeout=10)
    except sqlite3.Error:
        raise CalibreError("Couldn't open the Calibre library.") from None
    try:
        conn.row_factory = sqlite3.Row
        books = {r["id"]: {"id": r["id"], "title": r["title"], "path": r["path"], "authors": [], "epub": None}
                 for r in conn.execute("SELECT id, title, path FROM books")}
        for r in conn.execute("SELECT l.book, a.name FROM books_authors_link l JOIN authors a ON a.id = l.author "
                              "ORDER BY l.id"):
            if r["book"] in books:
                books[r["book"]]["authors"].append(r["name"])
        for r in conn.execute("SELECT book, name FROM data WHERE format = 'EPUB'"):
            book = books.get(r["book"])
            if book:
                book["epub"] = str(Path(library) / book["path"] / f"{r['name']}.epub")
    except sqlite3.Error:
        raise CalibreError("Couldn't read the Calibre library.") from None
    finally:
        conn.close()
    rows = []
    for book in books.values():
        book.pop("path")
        book["companion"] = companion_for(book["epub"])
        rows.append(book)
    return rows


def companion_for(epub):
    """`<epub stem>.companion.md` beside the ePub, when it exists and isn't empty."""
    if not epub:
        return None
    path = Path(epub).with_suffix(".companion.md")
    try:
        return str(path) if path.is_file() and path.stat().st_size > 0 else None
    except OSError:
        return None


def _norm(text):
    text = unicodedata.normalize("NFKD", text or "")
    text = "".join(c for c in text if not unicodedata.combining(c)).lower()
    text = re.split(r"[:(\[]", text, maxsplit=1)[0]
    return re.sub(r"[^a-z0-9]+", " ", text).strip()


def _surnames(authors):
    names = set()
    for author in re.split(r",| and |&|;|\n", authors or ""):
        words = _norm(author).split()
        if words:
            names.add(words[-1])
    return names


def match(title, author, books):
    """The Calibre book for a Readwise title and author, or None.

    Title and an author's surname first, then the title alone when only one book has it.
    """
    want = _norm(title)
    if not want:
        return None
    same_title = [b for b in books if _norm(b["title"]) == want]
    surnames = _surnames(author)
    with_author = [b for b in same_title if surnames & _surnames(", ".join(b["authors"]))]
    for group in (with_author, same_title):
        with_epub = [b for b in group if b["epub"]]
        if len(with_epub) == 1:
            return with_epub[0]
        if with_epub:
            return with_epub[0] if group is with_author else None
    return None
