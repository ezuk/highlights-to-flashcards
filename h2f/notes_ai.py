"""What the AI is asked while writing notes, and how its replies are read.

The AI never writes a note. It groups highlights into ideas, finds notes an idea sits near, and
suggests titles. Every title it names is checked against the vault.
"""

import json
import math
import random
import re

from . import notes as notes_module
from .cardgen import extract_json

LINK_KINDS = ("same idea", "disagrees", "builds on", "broader", "belongs in", "explains why")
MAX_IDEAS = 30
MAX_MISSED = 5
MAX_LINKS = 10
MAX_FLAGS = 2
MAX_TITLES = 4
TITLE_SAMPLE = 120
LINKS_FULL_NOTES = 40
FULL_NOTE_CHARS = 3000
_WORD = re.compile(r"[a-z][a-z'’-]{2,}")

GATHER_FORMAT = """Reply with JSON only, in this shape:
{"ideas": [{"title": "...", "summary": "...", "highlights": [1, 2],
            "close_to": [{"note": "exact note title", "why": "..."}],
            "disagrees": [{"note": "exact note title", "why": "..."}]}],
 "missed": [{"number": 1, "title": "...", "chapter": "...", "why": "...", "quote": "...",
             "close_to": [...], "disagrees": [...]}]}
When you are given a numbered list of ideas the reader didn't highlight, "missed" has one entry for each of
them, with its "number", a "summary" saying the idea in one plain sentence as the book puts it, and the notes
it is close to or disagrees with; leave out the other fields. Otherwise "missed" lists the ones you find, without a number, or is empty when you have
neither the book nor reference notes."""

LINKS_FORMAT = """Reply with JSON only, in this shape:
{"links": [{"note": "exact note title", "kind": "same idea", "why": "..."}]}
"kind" is one of: """ + ", ".join(LINK_KINDS) + "."

TITLES_FORMAT = """Reply with JSON only, in this shape:
{"titles": ["...", "..."]}"""


def _tagged(tag, body, intro=None):
    block = f"<{tag}>\n{body}\n</{tag}>"
    return f"{intro}\n{block}" if intro else block


def _system(prompt_text, reply_format):
    return {"role": "system", "content": prompt_text.strip() + "\n\n" + reply_format}


def vault_index(vault_notes, skip=()):
    """One line per note: its title, tags and opening words."""
    skipped = {s.lower() for s in skip}
    lines = []
    for note in sorted(vault_notes, key=lambda n: n["title"].lower()):
        if note["title"].lower() in skipped:
            continue
        tags = " ".join(note["tags"])
        words = notes_module.snippet(note["text"])
        lines.append(f"- {note['title']}" + (f" ({tags})" if tags else "") + (f": {words}" if words else ""))
    return "\n".join(lines)


def _words(text):
    return {w[:-1] if w.endswith("s") and not w.endswith("ss") else w for w in _WORD.findall(text.lower())}


def likely_notes(vault_notes, texts, per_text=5, limit=LINKS_FULL_NOTES, skip=()):
    """The notes sharing the most telling words with any of `texts`, best matches first: each
    text's top `per_text`, taken in turns until `limit`."""
    skipped = {s.lower() for s in skip}
    pool = [n for n in vault_notes if n["title"].lower() not in skipped]
    if not pool:
        return []
    titles = [_words(n["title"]) for n in pool]
    bodies = [_words(n["text"]) for n in pool]
    counts = {}
    for title, body in zip(titles, bodies):
        for word in title | body:
            counts[word] = counts.get(word, 0) + 1
    idf = {word: math.log(len(pool) / count) for word, count in counts.items()}
    ranked = []
    for text in texts:
        asked = _words(text)
        scores = [(sum(idf[w] for w in asked & body) / math.sqrt(1 + len(body))
                   + sum(idf[w] for w in asked & title), i)
                  for i, (title, body) in enumerate(zip(titles, bodies))]
        ranked.append([i for score, i in sorted(scores, reverse=True)[:per_text] if score > 0])
    chosen = []
    for turn in range(per_text):
        for row in ranked:
            if turn < len(row) and row[turn] not in chosen:
                chosen.append(row[turn])
            if len(chosen) >= limit:
                return [pool[i] for i in chosen]
    return [pool[i] for i in chosen]


def _full_notes(notes):
    parts = []
    for note in notes:
        text = note["text"].strip()
        if len(text) > FULL_NOTE_CHARS:
            text = text[:FULL_NOTE_CHARS].rsplit(" ", 1)[0] + " …"
        parts.append(f"### {note['title']}\n{text}")
    return "\n\n".join(parts)


def _highlight_rows(highlights, numbered=True):
    rows = []
    for number, h in enumerate(highlights, 1):
        where = h.get("chapter") or "Unknown chapter"
        row = (f"{number}. " if numbered else "- ") + f"[{where}] {h['text']}"
        if h.get("note"):
            row += "\n   Reader's margin note: " + h["note"]
        rows.append(row)
    return "\n".join(rows)


def _card_rows(cards):
    return "\n".join(f"- Q: {c['front']}\n  A: {c['back']}" for c in cards if c.get("front"))


def _about(book):
    lines = [f"Book: {book.get('title') or ''}"]
    if book.get("author"):
        lines.append(f"Author: {book['author']}")
    return _tagged("book", "\n".join(lines))


def build_gather(book, highlights, cards, companion, vault_notes, tag, prompt_text, missed=(), book_text=None):
    """Messages asking for the book's ideas: `highlights` numbered from 1. `missed` are ideas the
    reader didn't highlight, already found, to place in the slip-box."""
    parts = [_about(book)]
    if book_text:
        parts.append(_tagged("book", book_text, "The book itself:"))
    parts.append(_tagged("highlights", _highlight_rows(highlights), "Everything the reader highlighted, numbered:"))
    if cards:
        parts.append(_tagged("cards", _card_rows(cards), "Flashcards the reader made from these highlights:"))
    if companion:
        parts.append(_tagged("book_notes", companion, "Reference notes about the book:"))
    if missed:
        rows = "\n".join(f"{n}. {m['title']}: {m.get('summary') or ''}".rstrip(": ") for n, m in enumerate(missed, 1))
        parts.append(_tagged("not_highlighted", rows, "Key ideas of the book the reader didn't highlight, numbered:"))
    parts.append(_tagged("slipbox", _full_notes(vault_notes) or "(empty)",
                         f"Every note in the reader's slip-box, each under its title. Notes tagged {tag} are already "
                         "written from this book:"))
    parts.append("Reply with the JSON only.")
    return [_system(prompt_text, GATHER_FORMAT), {"role": "user", "content": "\n\n".join(parts)}]


def build_links(book, idea, highlights, cards, draft, index, prompt_text, full=()):
    parts = [_about(book), _tagged("idea", f"{idea['title']}\n{idea.get('summary') or ''}".strip())]
    if highlights:
        parts.append(_tagged("highlights", _highlight_rows(highlights, numbered=False), "The highlights behind it:"))
    if cards:
        parts.append(_tagged("cards", _card_rows(cards), "The reader's flashcards on it:"))
    if draft and draft.strip():
        parts.append(_tagged("draft", draft.strip(), "What the reader has written in the note so far:"))
    parts.append(_tagged("slipbox", index or "(empty)", "Every note in the reader's slip-box, by its opening words:"))
    if full:
        parts.append(_tagged("slipbox_notes", _full_notes(full),
                             "The whole text of the notes most likely to matter here:"))
    parts.append("Reply with the JSON only.")
    return [_system(prompt_text, LINKS_FORMAT), {"role": "user", "content": "\n\n".join(parts)}]


def build_titles(idea, highlights, draft, titles, prompt_text, seed=None):
    sample = list(titles)
    random.Random(seed).shuffle(sample)
    parts = [_tagged("idea", f"{idea['title']}\n{idea.get('summary') or ''}".strip())]
    if highlights:
        parts.append(_tagged("highlights", _highlight_rows(highlights, numbered=False), "The highlights behind it:"))
    if draft and draft.strip():
        parts.append(_tagged("draft", draft.strip(), "What the reader has written so far:"))
    parts.append(_tagged("titles", "\n".join(sample[:TITLE_SAMPLE]), "Titles from the reader's slip-box:"))
    parts.append("Reply with the JSON only.")
    return [_system(prompt_text, TITLES_FORMAT), {"role": "user", "content": "\n\n".join(parts)}]


# Replies


def _data(text):
    try:
        data = json.loads(extract_json(text) or "")
    except (TypeError, ValueError):
        return None
    return data if isinstance(data, dict) else None


def _text(item, key, limit=600):
    value = item.get(key) if isinstance(item, dict) else None
    return " ".join(value.split())[:limit] if isinstance(value, str) else ""


def _titles_map(vault_titles):
    return {t.lower(): t for t in vault_titles}


def _flags(items, known, kind):
    flags = []
    for item in items if isinstance(items, list) else []:
        title = known.get(_text(item, "note").lower())
        if title and title not in [f["note"] for f in flags]:
            flags.append({"kind": kind, "note": title, "why": _text(item, "why", 300)})
        if len(flags) >= MAX_FLAGS:
            break
    return flags


def parse_gather(text, highlight_count, vault_titles, missed_count=0):
    """({ideas, missed}, None) or (None, problem). Unknown note titles and highlight numbers
    are dropped. An idea with no highlights left is dropped."""
    data = _data(text)
    if data is None or not isinstance(data.get("ideas"), list):
        return None, "The AI's reply couldn't be read."
    known = _titles_map(vault_titles)
    ideas, used = [], set()
    for item in data["ideas"]:
        numbers = []
        for n in item.get("highlights") if isinstance(item, dict) and isinstance(item.get("highlights"), list) else []:
            if isinstance(n, int) and not isinstance(n, bool) and 1 <= n <= highlight_count and n not in numbers:
                numbers.append(n)
        title = _text(item, "title", 120)
        if not title or not numbers:
            continue
        flags = _flags(item.get("disagrees"), known, "disagrees") + _flags(item.get("close_to"), known, "close")
        ideas.append({"title": title, "summary": _text(item, "summary"), "highlights": numbers, "flags": flags})
        used.update(numbers)
        if len(ideas) >= MAX_IDEAS:
            break
    missed = []
    for item in data.get("missed") if isinstance(data.get("missed"), list) else []:
        if not isinstance(item, dict):
            continue
        flags = _flags(item.get("disagrees"), known, "disagrees") + _flags(item.get("close_to"), known, "close")
        number = item.get("number")
        if missed_count:
            if isinstance(number, int) and not isinstance(number, bool) and 1 <= number <= missed_count:
                missed.append({"number": number, "summary": _text(item, "summary"), "flags": flags})
            continue
        title = _text(item, "title", 120)
        if title:
            missed.append({"title": title, "chapter": _text(item, "chapter", 200), "why": _text(item, "why"),
                           "quote": _text(item, "quote", 1200), "flags": flags})
        if len(missed) >= MAX_MISSED:
            break
    return {"ideas": ideas, "missed": missed}, None


def parse_links(text, vault_titles, skip=()):
    data = _data(text)
    if data is None or not isinstance(data.get("links"), list):
        return None, "The AI's reply couldn't be read."
    known = _titles_map(vault_titles)
    skipped = {s.lower() for s in skip}
    links = []
    for item in data["links"]:
        title = known.get(_text(item, "note").lower())
        if not title or title.lower() in skipped or title in [link["note"] for link in links]:
            continue
        kind = _text(item, "kind", 40).lower()
        links.append({"note": title, "kind": kind if kind in LINK_KINDS else "same idea", "why": _text(item, "why", 400)})
        if len(links) >= MAX_LINKS:
            break
    return links, None


def parse_titles(text, vault_titles):
    data = _data(text)
    if data is None or not isinstance(data.get("titles"), list):
        return None, "The AI's reply couldn't be read."
    taken = {t.lower() for t in vault_titles}
    titles = []
    for value in data["titles"]:
        title = notes_module.clean_title(value) if isinstance(value, str) else ""
        if title and title.lower() not in taken and title.lower() not in {t.lower() for t in titles}:
            titles.append(title[:120])
        if len(titles) >= MAX_TITLES:
            break
    return titles, None
