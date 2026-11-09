"""Books as the screens see them: Readwise highlights joined to the Calibre ePub.

A book's source key is `readwise:<user_book_id>`, kept in the store's `device_path` column.
Each highlight's key is `rw:<highlight id>`. Where a highlight sits, its passage and its
chapter come from finding its words in the ePub.
"""

from datetime import datetime
from pathlib import Path

from . import calibre, epub
from . import save as save_module
from .store import book_id_for

NO_EPUB = "This book isn't in the Calibre library, so the AI sees only the highlights."
EPUB_UNREADABLE = "The book's ePub couldn't be read, so the AI sees only the highlights."
PAGE_TYPES = ("page", "order")


def source_key(readwise_id):
    return f"readwise:{readwise_id}"


def readwise_id(key):
    if not isinstance(key, str) or not key.startswith("readwise:"):
        return None
    try:
        return int(key.split(":", 1)[1])
    except ValueError:
        return None


def highlight_key(highlight_id):
    return f"rw:{highlight_id}"


def _stamp(text):
    try:
        return datetime.fromisoformat(text.replace("Z", "+00:00")).timestamp()
    except (AttributeError, ValueError):
        return None


def _page(row):
    value = row.get("location")
    if row.get("location_type") not in PAGE_TYPES:
        return None
    if isinstance(value, str) and value.isdigit():
        value = int(value)
    return value if isinstance(value, int) and value >= 0 else None


def linked(rw_book, calibre_books, links):
    """The Calibre book for a Readwise book: the user's own pick, else the best match."""
    picked = (links or {}).get(str(rw_book["id"]))
    if picked is not None:
        return next((b for b in calibre_books if b["id"] == picked), None)
    return calibre.match(rw_book["title"], rw_book["author"], calibre_books)


def _record(rw_book, match, last=None):
    epub_path = match["epub"] if match else None
    stem = Path(epub_path).stem if epub_path else rw_book["title"]
    return {
        "device_path": source_key(rw_book["id"]),
        "local_path": epub_path,
        "epub": epub_path,
        "file": Path(epub_path).name if epub_path else "",
        "calibre_id": match["id"] if match else None,
        "title": rw_book["title"] or stem,
        "author": rw_book["author"] or (", ".join(match["authors"]) if match else ""),
        "stem": stem,
        "ext": "epub" if epub_path else "",
        "last_read": _stamp(last) if last else None,
        "percent": None,
        "on_memory_card": False,
        "reachable_note": None,
        "missing": epub_path is None or not Path(epub_path).is_file(),
        "has_companion": bool(match and match.get("companion")),
        "companion_path": match.get("companion") if match else None,
        "sidecar_path": None,
        "sidecar_location": None,
        "counts": {"total": 0, "pending": 0, "reviewed": 0},
        "error": None if epub_path else NO_EPUB,
    }


def list_books(store, calibre_books, links):
    """Rows for Pick a book: every Readwise book with highlights, most recently highlighted first."""
    rows = []
    for rw_book in store.readwise_books():
        record = _record(rw_book, linked(rw_book, calibre_books, links), rw_book.get("last_highlight"))
        book_id = book_id_for(record["device_path"])
        texts = [{"key": highlight_key(h["id"]), "text": h["text"]} for h in store.readwise_highlights(rw_book["id"])]
        keys = {h["key"] for h in texts}
        reviewed = len(keys & save_module.reviewed_keys(store, {"id": book_id, **record}, texts))
        record["counts"] = {"total": len(keys), "pending": len(keys) - reviewed, "reviewed": reviewed}
        rows.append(record)
    return rows


def _percent_of(book, location):
    if not location.get("found"):
        return None
    lengths = [len(book.doc(n).text) for n in range(1, len(book.spine) + 1)]
    total = sum(lengths)
    if not total:
        return None
    before = sum(lengths[:location["doc"] - 1]) + location["start"]
    return round(100 * before / total, 1)


def _place(highlight, book):
    location = book.locate(highlight)
    highlight["location"] = location
    if location.get("found"):
        highlight["passage"] = book.passage(location)
        highlight["percent"] = _percent_of(book, location)
    highlight["chapter"] = book.toc_title_at(location) if location.get("found") else None
    chapter = book.chapter_for(highlight["chapter"], location)
    if chapter:
        highlight["chapter_number"] = chapter["number"]


def _chapter_list(book, highlights):
    """Table-of-contents chapters, with each highlight's chapter names filled in."""
    chapters = book.chapters() if book is not None else []
    by_number = {c["number"]: c for c in chapters if c["number"] is not None}
    by_title = {c["title"]: c for c in chapters}
    for chapter in chapters:
        chapter["count"] = 0
        chapter["pending"] = 0
    for highlight in highlights:
        chapter = by_number.get(highlight["chapter_number"]) or by_title.get(highlight["chapter"])
        highlight["chapter_name"] = chapter["name"] if chapter else highlight["chapter"]
        highlight["chapter_card"] = chapter["card"] if chapter else highlight["chapter"]
        if chapter:
            chapter["count"] += 1
            if highlight["status"] == "pending":
                chapter["pending"] += 1
    return chapters


def load_book(store, book_id, rw_book, match):
    """A book in full, in the shape the screens and runs use. Never raises."""
    rows = store.readwise_highlights(rw_book["id"])
    record = _record(rw_book, match, rows[-1]["highlighted_at"] if rows else None)
    record.update({"is_epub": bool(record["epub"]), "percent_finished": None, "doc_pages": None,
                   "card_history": save_module.card_history(store, book_id), "chapters": [], "highlights": []})
    book = None
    if record["epub"] and not record["missing"]:
        try:
            book = epub.open_epub(record["epub"])
        except epub.EpubError:
            record["error"] = EPUB_UNREADABLE
    reviewed = save_module.reviewed_keys(store, {"id": book_id, **record},
                                         [{"key": highlight_key(row["id"]), "text": row["text"]} for row in rows])
    highlights = []
    for row in rows:
        key = highlight_key(row["id"])
        h = {
            "key": key,
            "text": row["text"],
            "note": row["note"] or None,
            "chapter": None,
            "chapter_number": None,
            "page": _page(row),
            "position": "",
            "pos0": None,
            "pos1": None,
            "datetime": row["highlighted_at"],
            "percent": None,
            "status": "reviewed" if key in reviewed else "pending",
            "location": dict(epub.NOT_FOUND),
            "passage": None,
        }
        if book is not None:
            try:
                _place(h, book)
            except Exception:
                h["location"] = dict(epub.NOT_FOUND)
        if not h["location"].get("found"):
            h["passage"] = epub.empty_passage(h["text"])
        highlights.append(h)
    if book is not None:
        highlights.sort(key=lambda h: (h["location"].get("found") is not True, h["percent"] or 0))
        placed = [h["percent"] for h in highlights if h["percent"] is not None]
        if placed:
            record["percent_finished"] = max(placed) / 100
            record["percent"] = round(max(placed))
        if record["title"] == record["stem"] and book.title:
            record["title"] = book.title
    record["chapters"] = _chapter_list(book, highlights)
    record["highlights"] = highlights
    record["counts"] = {"total": len(highlights), "pending": sum(h["status"] == "pending" for h in highlights),
                        "reviewed": sum(h["status"] == "reviewed" for h in highlights)}
    return record
