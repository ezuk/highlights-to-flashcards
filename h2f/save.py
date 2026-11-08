"""The save preview and pipeline.

Functions take plain data, with no knowledge of HTTP:

book: {id?, device_path, title, author, file?}
highlight: {hid?, key, text, note?, chapter?, chapter_number?, page or pageno?, position?,
            reviewed? or status?}

A batch moves backed_up -> added (its notes are in the collection) -> synced (AnkiWeb has
them). Every step can be repeated, so a crash at any point resumes safely: see finish_batch.
"""

import json
import logging
import re
import threading
from collections import Counter

from . import cardgen, epub
from . import collection as collection_module
from . import store as store_module
from .store import ADDED, BACKED_UP, SYNCED

WAITING = "waiting"
FAILED = "failed"

MAX_PAGE = 2**32 - 1

_CLOZE_GAP = re.compile(r"\{\{c\d+::(.*?)\}\}")
# Anki's invalid_char_for_field
_FIELD_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_log = logging.getLogger(__name__)

# One save at a time, across the Save button, Try again and Sync
_write_lock = threading.Lock()


class SaveBlocked(Exception):
    """This save can't start. The message is shown to the user."""


# Field values


def _text(value):
    return "" if value is None else str(value)


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def page_number(item):
    """The page as a whole number, or None."""
    for field in ("pageno", "page"):
        value = item.get(field)
        if isinstance(value, bool):
            continue
        if isinstance(value, float) and value.is_integer():
            value = int(value)
        if isinstance(value, str) and value.isascii() and value.isdigit():
            value = int(value)
        if isinstance(value, int) and 0 <= value <= MAX_PAGE:
            return value
    return None


def position_of(item):
    """Where the highlight is, as the source gave it, or "" when it gave none."""
    position = item.get("position")
    if isinstance(position, str):
        return position
    pos0 = item.get("pos0")
    if isinstance(pos0, str):
        return pos0
    if isinstance(pos0, dict) and pos0.get("page") is not None:
        return str(pos0["page"])
    return ""


def chapter_name(item):
    """The chapter as cards show it, or "" when the highlight has none."""
    title = _text(item.get("chapter")).strip()
    return epub.chapter_card(item.get("chapter_number"), title) if title else ""


def deck_name(title, parent):
    """The deck a book's cards go in."""
    return collection_module.deck_name(parent, _text(title))


def _author(book):
    return _text(book.get("author")).replace("\n", ", ")


def _encode(value):
    text = json.dumps(value, ensure_ascii=False, separators=(",", ":"))
    return text.encode("utf-8", "replace").decode("utf-8")


# Lines


def _file(book):
    """The ePub's file name, from a book record or a stored book row."""
    return _text(book.get("file") or (book.get("data") or {}).get("file"))


def _inbox_line(book, batch_id, front, back, quote, chapter, page, position):
    card = {
        "front": front,
        "back": back,
        "quote": quote,
        "book": _text(book.get("title")),
        "author": _author(book),
        "chapter": chapter,
    }
    if page is not None:
        card["page"] = page
    card["file"] = _file(book)
    card["position"] = position
    card["batch"] = batch_id
    return _encode(card)


def highlight_card_line(book, highlight, card, batch_id):
    return _inbox_line(
        book,
        batch_id,
        card["front"],
        card["back"],
        _text(highlight.get("text")),
        chapter_name(highlight),
        page_number(highlight),
        position_of(highlight),
    )


def _source_line(highlight):
    page = page_number(highlight)
    place = " · ".join(p for p in (chapter_name(highlight), "" if page is None else f"p. {page}") if p)
    text = " ".join(_text(highlight.get("text")).split())
    return f'{place}: "{text}"' if place else f'"{text}"'


def connection_chapters(connection, by_hid):
    """Chapter names of a connection's sources, each once, joined with ` · `."""
    names = []
    for hid in connection["sources"]:
        name = chapter_name(by_hid[hid]) if hid in by_hid else ""
        if name and name not in names:
            names.append(name)
    return " · ".join(names)


def connection_line(book, connection, by_hid, batch_id):
    sources = [by_hid[hid] for hid in connection["sources"] if hid in by_hid]
    return _inbox_line(
        book,
        batch_id,
        connection["front"],
        connection["back"],
        "\n".join(_source_line(h) for h in sources),
        connection_chapters(connection, by_hid),
        None,
        "",
    )


def idea_line(book, idea, batch_id):
    return _inbox_line(book, batch_id, idea["front"], idea["back"], "", _text(idea.get("chapter")), None, "")


def reviewed_line(book, highlight, cards, batch_id):
    """The line marking one highlight reviewed, with its cards for the AI's history.
    Definition cards stay out of the history."""
    quote = _text(highlight.get("text"))
    note = _text(highlight.get("note"))
    entries = []
    for card in cards:
        if card["kind"] == "def":
            continue
        entry = {"front": card["front"], "back": card["back"]}
        if card.get("suggested") is not None:
            entry["suggested"] = {"front": card["suggested"]["front"], "back": card["suggested"]["back"]}
        entry["quote"] = quote
        if note:
            entry["note"] = note
        entries.append(entry)
    return _encode(
        {"v": 1, "batch": batch_id, "key": highlight["key"], "cards": entries}
    )


# What gets saved


def _book_id(book):
    return book.get("id") or store_module.book_id_for(book["device_path"])


def _hid(highlight):
    return highlight.get("hid") or store_module.hid_for(highlight["key"])


def _filled(item):
    return item["front"].strip() != ""


def _cloze_sound(front):
    """Whether a cloze hides some words and has no stray braces around its hidden parts."""
    gaps = _CLOZE_GAP.findall(front)
    rest = _CLOZE_GAP.sub("", front)
    return any(gap.strip() for gap in gaps) and "{{" not in rest and "}}" not in rest


def need_of(item):
    """What a written card lacks before it can be answered: "cloze", "answer" or None."""
    front = item["front"]
    if item.get("kind") == "cloze" or cardgen.is_cloze(front) or "{{c" in front:
        return None if _cloze_sound(front) else "cloze"
    return None if item["back"].strip() else "answer"


def _ready(item):
    """Written and answered. A card with no answer hasn't been reviewed, so it stays out of a save."""
    return _filled(item) and need_of(item) != "answer"


def _select(store, book, highlights, mark_connection_sources):
    """What a save covers, by the mock's rules: only what the user opened."""
    book_id = _book_id(book)
    states = store.list_highlights(book_id)
    in_flight = _in_flight_keys(store, book_id)
    drafts = {}
    for card in store.list_cards(book_id):
        drafts.setdefault(card["hid"], []).append(card)
    by_hid = {_hid(h): h for h in highlights}

    pending, skipped = [], []
    for hid, h in by_hid.items():
        if h.get("reviewed") or h.get("status") == "reviewed" or h["key"] in in_flight:
            continue
        (skipped if states.get(hid, {}).get("skipped") else pending).append(hid)
    pending_set = set(pending)
    skipped_cards = sum(1 for hid in skipped for card in drafts.get(hid, []) if _filled(card))

    with_cards = []
    for hid in pending:
        if states.get(hid, {}).get("opened") and any(_ready(c) for c in drafts.get(hid, [])):
            with_cards.append(hid)
    unopened = [
        hid for hid in pending if not states.get(hid, {}).get("opened") and drafts.get(hid)
    ]

    all_connections = store.list_connections(book_id)
    all_ideas = store.list_ideas(book_id)
    connections = [c for c in all_connections if c["opened"] and _ready(c)]
    ideas = [i for i in all_ideas if i["opened"] and _ready(i)]
    no_answer = sum(1 for hid in pending if states.get(hid, {}).get("opened")
                    for card in drafts.get(hid, []) if _filled(card) and not _ready(card))
    no_answer += sum(1 for item in (*all_connections, *all_ideas) if item["opened"] and _filled(item) and not _ready(item))

    removed = {card["hid"] for card in store.list_cards(book_id, None, (store_module.REMOVED,)) if card["suggested"] is not None}
    emptied = [
        hid for hid in pending
        if hid in removed and not states.get(hid, {}).get("redo") and not any(_filled(c) for c in drafts.get(hid, []))
    ]

    connection_only = []
    for connection in connections:
        for hid in connection["sources"]:
            if (
                hid in pending_set
                and hid not in connection_only
                and not any(_filled(c) for c in drafts.get(hid, []))
            ):
                connection_only.append(hid)
    marked_sources = connection_only if mark_connection_sources else []

    needs, need_kinds = [], []
    for hid in with_cards:
        for card in drafts[hid]:
            need = need_of(card) if _ready(card) else None
            if need:
                needs.append(hid)
                need_kinds.append(need)
    need_connections = [c["id"] for c in connections if need_of(c)]
    need_ideas = [i["id"] for i in ideas if need_of(i)]
    need_kinds += ["answer"] * (len(need_connections) + len(need_ideas))
    count = sum(sum(1 for c in drafts[hid] if _ready(c)) for hid in with_cards) + len(connections) + len(ideas)

    return {
        "by_hid": by_hid,
        "drafts": drafts,
        "with_cards": with_cards,
        "emptied": emptied,
        "connection_only": connection_only,
        "marked_sources": marked_sources,
        "skipped": skipped,
        "skipped_cards": skipped_cards,
        "no_answer": no_answer,
        "pending": pending,
        "unopened": unopened,
        "connections": connections,
        "ideas": ideas,
        "connections_waiting": sum(1 for c in all_connections if not c["opened"]),
        "ideas_waiting": sum(1 for i in all_ideas if not i["opened"]),
        "needs": needs,
        "need_connections": need_connections,
        "need_ideas": need_ideas,
        "need_kinds": need_kinds,
        "count": count,
        "mark_count": len(with_cards) + len(skipped) + len(marked_sources),
        "left": len(pending) - len(with_cards) - len(marked_sources),
    }


def _need_text(kinds):
    answers, clozes = kinds.count("answer"), kinds.count("cloze")
    parts = []
    if answers:
        verb = "needs an answer" if answers == 1 else "need answers"
        parts.append(f"{_plural(answers, 'question')} still {verb}.")
    if clozes:
        verb = "needs" if clozes == 1 else "need"
        parts.append(f"{_plural(clozes, 'cloze card')} still {verb} words hidden correctly.")
    fix = "Answer or remove before saving." if not clozes else "Fix or remove before saving."
    return " ".join([*parts, fix])


def _left_text(selection):
    parts = []
    left = selection["left"]
    parts.append(f"{_plural(left, 'highlight')} will stay to do." if left > 0 else "Every pending highlight is covered.")
    unopened = len(selection["unopened"])
    if unopened == 1:
        parts.append("1 highlight with suggestions you haven't opened is not included. Its suggestions stay here for later.")
    elif unopened:
        parts.append(
            f"{unopened} highlights with suggestions you haven't opened are not included. "
            "Their suggestions stay here for later."
        )
    for n, word in ((selection["connections_waiting"], "connection"), (selection["ideas_waiting"], "key idea")):
        if n:
            verb = "stays" if n == 1 else "stay"
            parts.append(f"{_plural(n, word)} you haven't opened also {verb} here.")
    n = selection["no_answer"]
    if n:
        parts.append("1 card with no answer is not included." if n == 1 else f"{n} cards with no answer are not included.")
    n = selection["skipped_cards"]
    if n == 1:
        parts.append("1 card on a skipped highlight will not be saved. Undo the skip to keep it.")
    elif n:
        parts.append(f"{n} cards on skipped highlights will not be saved. Undo the skip to keep them.")
    return " ".join(parts)


def _where(highlight):
    page = page_number(highlight)
    number = highlight.get("chapter_number")
    title = _text(highlight.get("chapter")).strip()
    chapter = f"Ch. {number}" if number and not epub.is_numbered_title(title) else title
    return " · ".join(p for p in (chapter, "" if page is None else f"p. {page}") if p)


_NEED_LABELS = {"answer": "No answer yet", "cloze": "No words hidden"}


def _preview_row(card):
    need = need_of(card)
    label = {"need": bool(need), **({"needLabel": _NEED_LABELS[need]} if need else {})}
    if card.get("kind") == "cloze":
        return {"front": _CLOZE_GAP.sub("[...]", card["front"]), "back": "Cloze", **label}
    return {"front": card["front"], "back": card["back"], **label}


def _editable(row, card, base):
    # A row of the Save cards screen with what it takes to change the card
    is_cloze = card.get("kind") == "cloze"
    return {**row, "path": f"/api/{base}/{card['id']}", "cloze": is_cloze, "front_raw": card["front"], "back_raw": card["back"]}


def _editable_groups(selection):
    # _groups with the raw text and address of each draft, for editing before a save
    groups = _groups(selection)
    cards = {}
    for hid in selection["with_cards"]:
        cards[("highlight", hid)] = [c for c in selection["drafts"][hid] if _ready(c)]
    for c in selection["connections"]:
        cards[("connection", c["id"])] = [c]
    for i in selection["ideas"]:
        cards[("idea", i["id"])] = [i]
    bases = {"highlight": "cards", "connection": "connections", "idea": "ideas"}
    for group in groups:
        items = cards[(group["type"], group["id"])]
        group["rows"] = [_editable(row, card, bases[group["type"]]) for row, card in zip(group["rows"], items)]
    by_hid = selection["by_hid"]
    for hid in selection["emptied"]:
        h = by_hid[hid]
        groups.append({"type": "highlight", "id": hid, "head": _text(h.get("text")), "where": _where(h), "rows": [], "emptied": True})
    return groups


def _groups(selection):
    by_hid = selection["by_hid"]
    groups = []
    for hid in selection["with_cards"]:
        h = by_hid[hid]
        rows = [_preview_row(c) for c in selection["drafts"][hid] if _ready(c)]
        groups.append({"type": "highlight", "id": hid, "head": _text(h.get("text")), "where": _where(h), "rows": rows})
    for c in selection["connections"]:
        groups.append(
            {
                "type": "connection",
                "id": c["id"],
                "head": f"Connection: {c['title']}",
                "where": connection_chapters(c, by_hid),
                "rows": [_preview_row(c)],
            }
        )
    for i in selection["ideas"]:
        groups.append(
            {
                "type": "idea",
                "id": i["id"],
                "head": f"Key idea: {i['title']}",
                "where": _text(i.get("chapter")),
                "rows": [_preview_row(i)],
            }
        )
    return groups


def _noun(count):
    return "card ready" if count == 1 else "cards ready"


def _mark_text(count):
    return f"Mark {_plural(count, 'highlight')} as reviewed"


def preview(store, book, highlights, mark_connection_sources=True):
    """The Save cards screen: what goes to the collection, what gets marked and what stays."""
    selection = _select(store, book, highlights, mark_connection_sources)
    count = selection["count"]
    needs = selection["needs"]
    kinds = selection["need_kinds"]
    return {
        "groups": _editable_groups(selection),
        "count": count,
        "noun": _noun(count),
        "needs": needs,
        "need_connections": selection["need_connections"],
        "need_ideas": selection["need_ideas"],
        "need_text": _need_text(kinds) if kinds else "",
        "blocked": bool(kinds) or count == 0,
        "mark_count": selection["mark_count"],
        "mark_text": _mark_text(selection["mark_count"]),
        "marked": {
            "with_cards": selection["with_cards"],
            "connection_only": selection["marked_sources"],
            "skipped": selection["skipped"],
        },
        "connection_only": selection["connection_only"],
        "left": selection["left"],
        "left_text": _left_text(selection),
    }


def with_held(preview_view, held):
    """`preview_view` plus the cards of `held` batch results, which waited or failed and so are
    no longer drafts. `blocked`, `needs` and `marked` still describe only what can be saved now."""
    if not held:
        return preview_view
    count = preview_view["count"] + sum(result["summary"].get("cards", 0) for result in held)
    mark_count = preview_view["mark_count"] + sum(result["summary"].get("marked", 0) for result in held)
    groups = [group for result in held for group in result["summary"].get("groups", [])]
    return {
        **preview_view,
        "groups": groups + preview_view["groups"],
        "count": count,
        "noun": _noun(count),
        "mark_count": mark_count,
        "mark_text": _mark_text(mark_count),
    }


def back_up(store, book, highlights, mark_connection_sources=True, deck_parent=""):
    """Step 1: writes the batch to SQLite and turns its drafts `saved`, so they can't be saved twice."""
    book_id = _book_id(book)
    with store.transaction():
        selection = _select(store, book, highlights, mark_connection_sources)
        if selection["need_kinds"]:
            raise SaveBlocked(_need_text(selection["need_kinds"]))
        if selection["count"] == 0:
            batches = store.list_batches(book_id)
            if any(batch["status"] == BACKED_UP for batch in batches):
                raise SaveBlocked("These cards are already being saved.")
            if batches:
                raise SaveBlocked("These cards are already saved. There's nothing new to save.")
            raise SaveBlocked("There are no cards to save yet.")
        by_hid, drafts = selection["by_hid"], selection["drafts"]
        batch_id = store_module.new_id()

        inbox_lines, reviewed_lines, saved_cards, card_ids, blank_ids = [], [], [], [], []
        for hid in selection["with_cards"]:
            h = by_hid[hid]
            cards = [c for c in drafts[hid] if _ready(c)]
            blank_ids.extend(c["id"] for c in drafts[hid] if not _ready(c))
            for card in cards:
                inbox_lines.append(highlight_card_line(book, h, card, batch_id))
                saved_cards.append({**card, "hid": hid})
                card_ids.append(card["id"])
            reviewed_lines.append(reviewed_line(book, h, cards, batch_id))
        for hid in selection["skipped"] + selection["marked_sources"]:
            reviewed_lines.append(reviewed_line(book, by_hid[hid], [], batch_id))
        for hid in selection["skipped"]:
            blank_ids.extend(c["id"] for c in drafts.get(hid, []))
        for connection in selection["connections"]:
            inbox_lines.append(connection_line(book, connection, by_hid, batch_id))
        for idea in selection["ideas"]:
            inbox_lines.append(idea_line(book, idea, batch_id))

        summary = {
            "cards": selection["count"],
            "highlight_cards": len(card_ids),
            "connections": len(selection["connections"]),
            "ideas": len(selection["ideas"]),
            "marked": selection["mark_count"],
            "skipped": len(selection["skipped"]),
            "title": _text(book.get("title")),
            "deck": deck_name(book.get("title"), deck_parent),
            "file": _file(book),
            "groups": _groups(selection),
        }
        store.record_batch(
            batch_id,
            book_id,
            inbox_lines,
            reviewed_lines,
            summary,
            saved_cards=saved_cards,
            card_ids=card_ids,
            blank_card_ids=blank_ids,
            connection_ids=[c["id"] for c in selection["connections"]],
            idea_ids=[i["id"] for i in selection["ideas"]],
            marked_hids=selection["with_cards"] + selection["skipped"] + selection["marked_sources"],
        )
    return batch_id


# Adding to the collection and syncing


def _in_flight_keys(store, book_id):
    """Highlight keys in this book's batches not yet in the collection."""
    keys = set()
    for batch in store.list_batches(book_id, (BACKED_UP,)):
        for line in batch["reviewed_lines"]:
            keys.add(json.loads(line)["key"])
    return keys


def batch_result(batch):
    """A batch as the screens see it: `synced`, `added` (in the collection, not on AnkiWeb yet),
    `waiting`, or `failed` with a sentence."""
    if batch["status"] in (SYNCED, ADDED):
        status = batch["status"]
    elif batch["error"]:
        status = FAILED
    else:
        status = WAITING
    return {
        "batch": batch["id"],
        "book_id": batch["book_id"],
        "status": status,
        "error": batch["error"],
        "summary": batch["summary"],
    }


def _already_added(collection, batch, cards):
    """{front field: note id} for notes an earlier try at this batch added before a crash."""
    since = int(batch["created_at"] * 1000)
    notes, _graves = collection.notes()
    fronts = {collection_module.text_to_html(card["front"]) for card in cards}
    return {n["raw_front"]: n["nid"] for n in notes if n["nid"] >= since and n["raw_front"] in fronts}


def add_batch(store, collection, batch_id, options):
    """Puts a backed-up batch's notes in the collection. Safe to call any number of times."""
    with _write_lock:
        batch = store.get_batch(batch_id)
        if batch is None:
            raise store_module.NotFound(batch_id)
        if batch["status"] != BACKED_UP:
            return batch_result(batch)
        cards = [json.loads(line) for line in batch["inbox_lines"]]
        try:
            found = _already_added(collection, batch, cards)
            missing = [c for c in cards if collection_module.text_to_html(c["front"]) not in found]
            ids = list(found.values()) + collection.add_cards(
                missing, notetype=options.get("notetype"), deck_parent=options.get("deck_parent"),
                tags=options.get("tags"))
        except collection_module.CollectionError as e:
            store.set_batch_error(batch_id, str(e))
        except Exception:
            _log.exception("adding batch %s failed", batch_id)
            store.set_batch_error(batch_id, "Something went wrong while adding the cards. They're safe here.")
        else:
            store.mark_batch_added(batch_id, ids)
        return batch_result(store.get_batch(batch_id))


def add_pending(store, collection, options, book_id=None):
    return [add_batch(store, collection, b["id"], options) for b in store.list_batches(book_id, (BACKED_UP,))]


def sync(store, collection):
    """Syncs with AnkiWeb and marks added batches synced. Returns None, or the sentence why not."""
    with _write_lock:
        added = store.list_batches(None, (ADDED,))
        try:
            collection.sync()
        except collection_module.CollectionError as e:
            for batch in added:
                store.set_batch_error(batch["id"], str(e))
            return str(e)
        store.mark_batches_synced([b["id"] for b in added])
        return None


def pending_saves(store, book_id=None):
    """Batches not yet on AnkiWeb, as the screens see them."""
    return [batch_result(b) for b in store.unsynced_batches(book_id)]


def save(store, collection, book, highlights, options, mark_connection_sources=True):
    """The whole pipeline. Returns batch_result for the new batch; SaveBlocked if it can't start."""
    batch_id = back_up(store, book, highlights, mark_connection_sources, options.get("deck_parent") or "")
    add_pending(store, collection, options)
    if collection.logged_in():
        sync(store, collection)
    return batch_result(store.get_batch(batch_id))


def reviewed_keys(store, book):
    """Reviewed status for display: highlights whose save is in the collection, or under way."""
    book_id = _book_id(book)
    return store.reviewed_keys(book_id) | _in_flight_keys(store, book_id)


def card_history(store, book_id):
    """Cards saved from this book's highlights, oldest first, as the AI's history entries."""
    history = []
    for batch in store.list_batches(book_id, (ADDED, SYNCED)):
        for line in batch["reviewed_lines"]:
            history.extend(json.loads(line).get("cards") or [])
    return history
