"""Changes to cards in the collection, made from this app.

A change applies to the app's copy of the collection at once and goes to AnkiWeb with the
next sync. Each one is kept in SQLite so it can be undone, and so a sync that puts the old
text back is noticed and shown.
"""

import html
import json
import re
import unicodedata

from . import store as store_module

APPLIED, SYNCED = "applied", "synced"
STAGES = (APPLIED, SYNCED)
EDIT, SUSPEND, UNSUSPEND, DELETE = "edit", "suspend", "unsuspend", "delete"
UNDONE = "undone"

ERROR_SETTING = "changes_error"

EDITABLE_NOTETYPES = ("Basic", "Cloze", "pbAnki KOReader", "pbAnki KOReader Cloze",
                      "Highlights to Flashcards", "Highlights to Flashcards Cloze")
CLOZE_NOTETYPES = ("Cloze", "pbAnki KOReader Cloze", "Highlights to Flashcards Cloze")
LINE_TAGS = ("br", "div", "p")

CLOZE_ERROR = ("Keep the same cloze numbers. Changing them would add a new card or leave an empty one behind. "
               "To hide a different part, add a new card instead.")

_BREAK = re.compile(r"<br\s*/?>|</div>|</p>", re.I)
_TAG = re.compile(r"<[^>]+>")
_SPACE = re.compile(r"\s+")
_CLOZE = re.compile(r"\{\{c\d+::(.*?)(::.*?)?\}\}")
_CLOZE_NUMBER = re.compile(r"\{\{c(\d+)::")
_CONTROL = re.compile(r"[\x00-\x08\x0b-\x1f\x7f]")

_STEP_NAMES = ("In your collection", "On AnkiWeb")
_STEP_NOW = ("Changed here. It goes to AnkiWeb with the next sync.", "On AnkiWeb.")
_REFUSED = {
    "changed": "This card changed in Anki since it was last read here. Sync, look again, then edit it.",
    "gone": "That card isn't in your collection any more.",
    "formatted": "This side has formatting. Edit it in Anki so it isn't lost.",
    "cloze": CLOZE_ERROR,
    "empty": "A card needs a question.",
    "note_type": "This kind of card can only be edited in Anki.",
    "several_cards": "This note makes several cards, so it can only be edited in Anki.",
}


class ChangeRefused(Exception):
    """This change can't be made. The message is shown to the user."""


def plain(text):
    """A field as plain text. Cloze deletions become [brackets]."""
    text = _BREAK.sub(" ", text)
    text = _TAG.sub("", text)
    text = html.unescape(text)
    text = _CLOZE.sub(r"[\1]", text)
    return _SPACE.sub(" ", text).strip()


# Field text, as common/src/edit.rs and common/src/inbox.rs read and write it


def _tag_name(tag):
    parts = tag.lstrip("/").replace("/", " ").split()
    return parts[0].lower() if parts else ""


def has_formatting(raw):
    """Whether a field holds more than text and line breaks: has_formatting in common/src/edit.rs."""
    rest = raw or ""
    while (start := rest.find("<")) >= 0:
        end = rest.find(">", start)
        if end < 0:
            return False
        if _tag_name(rest[start + 1:end]) not in LINE_TAGS:
            return True
        rest = rest[end + 1:]
    return "[sound:" in (raw or "")


def field_text(raw):
    """A field as typed text: field_to_text in common/src/edit.rs."""
    text = ""
    rest = raw or ""
    while (start := rest.find("<")) >= 0:
        text += rest[:start]
        end = rest.find(">", start)
        if end < 0:
            text += rest[start:]
            rest = ""
            break
        tag = rest[start + 1:end]
        name = _tag_name(tag)
        opens_block = not tag.startswith("/") and name != "br"
        if name in LINE_TAGS and not (opens_block and (not text or text.endswith("\n"))):
            text += "\n"
        rest = rest[end + 1:]
    return html.unescape(text + rest).replace("\xa0", " ").strip()


def clean_typed(text):
    """Typed text as it can be stored: Anki's normalize_field on a field."""
    return unicodedata.normalize("NFC", _CONTROL.sub("", text or "")).strip()


def to_raw(text):
    """The field stored for typed text, as Anki would store it."""
    return clean_typed(text).replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;").replace("\n", "<br>")


def _plain(raw):
    return plain(raw or "")


def cloze_numbers(text):
    return set(_CLOZE_NUMBER.findall(text or ""))


# What can be changed


def locks(note):
    """What stops `note` being edited or suspended here."""
    has_raw = note.get("raw_front") is not None
    other_type = has_raw and (note.get("notetype") not in EDITABLE_NOTETYPES or (note.get("fields") or 0) < 2)
    several = has_raw and (note.get("cards") or 0) != 1
    front_locked = has_formatting(note.get("raw_front"))
    back_locked = has_formatting(note.get("raw_back"))
    note_text = ""
    if not has_raw:
        note_text = "Sync with AnkiWeb once to edit this card."
    elif other_type or several:
        note_text = "Edit this card in Anki."
    elif front_locked and back_locked:
        note_text = "This card has formatting. Edit it in Anki so it isn't lost."
    return {
        "canEdit": has_raw and not other_type and not several and not (front_locked and back_locked),
        "canSuspend": has_raw,
        "frontLocked": front_locked,
        "backLocked": back_locked,
        "lockNote": note_text,
        "isCloze": note.get("notetype") in CLOZE_NOTETYPES,
    }


# Making changes


def live(rows):
    """Changes still open, without a problem."""
    return [row for row in rows if row["closed_at"] is None and not row["problem"]]


def overlay(notes, rows):
    """`notes` unchanged: changes apply to the collection at once, so a fresh read already has them."""
    return list(notes)


def _new_row(op, note, now, **fields):
    return {"id": store_module.new_id(), "op": op, "nid": note["nid"], "deck": note.get("deck") or "",
            "expect_front": note.get("raw_front"), "expect_back": note.get("raw_back"),
            "stage": APPLIED, "created_at": now, **fields}


def _note_or_refuse(notes, nid):
    note = next((n for n in notes if n["nid"] == nid), None)
    if note is None:
        raise ChangeRefused(_REFUSED["gone"])
    return note


def _apply(collection, op, note, **fields):
    outcome = collection.apply(op, note["nid"], note.get("raw_front"), note.get("raw_back"), **fields)
    if outcome not in ("applied", "already"):
        raise ChangeRefused(_REFUSED.get(outcome, "That change couldn't be made."))
    return outcome


def edit(store, collection, notes, nid, front, back, learn, now):
    """Rewords a card. Returns the row, or None when the text is what the card already has."""
    note = _note_or_refuse(notes, nid)
    lock = locks(note)
    if not lock["canEdit"]:
        raise ChangeRefused(lock["lockNote"] or "This card can't be edited here.")
    front, back = clean_typed(front), clean_typed(back)
    if not front:
        raise ChangeRefused(_REFUSED["empty"])
    front_changed = front != field_text(note["raw_front"])
    back_changed = back != field_text(note["raw_back"])
    if not front_changed and not back_changed:
        return None
    if front_changed and lock["frontLocked"] or back_changed and lock["backLocked"]:
        raise ChangeRefused(_REFUSED["formatted"])
    if lock["isCloze"] and front_changed and cloze_numbers(front) != cloze_numbers(note["raw_front"]):
        raise ChangeRefused(CLOZE_ERROR)
    new_front = front if front_changed else None
    new_back = back if back_changed else None
    _apply(collection, EDIT, note, front=new_front, back=new_back, learn=bool(learn))
    row = _new_row(EDIT, note, now, front=new_front, back=new_back, learn=int(bool(learn)))
    store.add_card_change(row)
    return row


def suspend(store, collection, notes, nid, now):
    note = _note_or_refuse(notes, nid)
    if note.get("suspended"):
        raise ChangeRefused("This card is already suspended.")
    _apply(collection, SUSPEND, note)
    row = _new_row(SUSPEND, note, now)
    store.add_card_change(row)
    return row


def unsuspend(store, collection, notes, nid, now):
    note = _note_or_refuse(notes, nid)
    _apply(collection, UNSUSPEND, note)
    rows = store.card_changes(open_only=True)
    earlier = next((r for r in reversed(live(rows)) if r["nid"] == nid and r["op"] == SUSPEND), None)
    row = _new_row(UNSUSPEND, note, now, replaces=earlier["id"] if earlier else None)
    with store.transaction():
        if earlier:
            store.update_card_change(earlier["id"], closed_at=now)
        store.add_card_change(row)
    return row


def delete(store, collection, notes, nid, now):
    """Deletes a suspended card for good, after keeping a copy of its text here."""
    note = _note_or_refuse(notes, nid)
    if not note.get("suspended"):
        raise ChangeRefused("Suspend this card first.")
    _apply(collection, DELETE, note)
    rows = store.card_changes(open_only=True)
    earlier = next((r for r in reversed(live(rows)) if r["nid"] == nid and r["op"] == SUSPEND), None)
    copy = {key: note.get(key) for key in ("nid", "deck", "front", "back", "raw_front", "raw_back", "notetype", "tags")}
    row = _new_row(DELETE, note, now, replaces=earlier["id"] if earlier else None,
                   copy_json=json.dumps(copy, ensure_ascii=False))
    with store.transaction():
        if earlier:
            store.update_card_change(earlier["id"], closed_at=now)
        store.add_card_change(row)
    return row


def can_undo(row):
    return row["closed_at"] is None and not row["problem"] and row["op"] != DELETE


def undo(store, collection, change_id, now):
    """Puts a card back as it was before this change."""
    row = store.card_change(change_id)
    if row is None or not can_undo(row):
        raise ChangeRefused("That change can't be taken back.")
    nid = row["nid"]
    if row["op"] == EDIT:
        current_front = to_raw(row["front"]) if row["front"] is not None else None
        current_back = to_raw(row["back"]) if row["back"] is not None else None
        outcome = collection.apply(EDIT, nid, current_front, current_back,
                                   front=field_text(row["expect_front"]) if row["front"] is not None else None,
                                   back=field_text(row["expect_back"]) if row["back"] is not None else None)
    elif row["op"] == SUSPEND:
        outcome = collection.apply(UNSUSPEND, nid)
    else:
        outcome = collection.apply(SUSPEND, nid)
    if outcome not in ("applied", "already"):
        raise ChangeRefused(_REFUSED.get(outcome, "That change can't be taken back."))
    with store.transaction():
        store.remove_card_change(change_id)
        if row["replaces"]:
            store.update_card_change(row["replaces"], closed_at=None)
    return row


def use_mine(store, collection, notes, change_id, now):
    """Makes a change again over what the card has now, after a sync put the old text back."""
    row = store.card_change(change_id)
    if row is None or not row["problem"]:
        raise ChangeRefused("That change isn't waiting on you any more.")
    note = _note_or_refuse(notes, row["nid"])
    with store.transaction():
        store.update_card_change(change_id, closed_at=now)
    if row["op"] == EDIT:
        return edit(store, collection, notes, row["nid"],
                    row["front"] if row["front"] is not None else field_text(note["raw_front"]),
                    row["back"] if row["back"] is not None else field_text(note["raw_back"]), row["learn"], now)
    if row["op"] == SUSPEND:
        return suspend(store, collection, notes, row["nid"], now)
    if row["op"] == UNSUSPEND:
        return unsuspend(store, collection, notes, row["nid"], now)
    return delete(store, collection, notes, row["nid"], now)


def drop(store, change_id, now):
    row = store.card_change(change_id)
    if row is None or not row["problem"]:
        raise ChangeRefused("That change isn't waiting on you any more.")
    store.update_card_change(change_id, closed_at=now)
    return row


# After a sync


def _settled_edit(store, row, note, now):
    targets = [(to_raw(row["front"]), note["raw_front"]) if row["front"] is not None else None,
               (to_raw(row["back"]), note["raw_back"]) if row["back"] is not None else None]
    if all(pair is None or pair[0] == pair[1] for pair in targets):
        if note.get("usn") != -1 and row["stage"] != SYNCED:
            store.update_card_change(row["id"], stage=SYNCED, synced_at=now, on_ankiweb=1)
    elif note["raw_front"] == row["expect_front"] and note["raw_back"] == row["expect_back"]:
        store.update_card_change(row["id"], problem=UNDONE, reader_front=note["raw_front"], reader_back=note["raw_back"])
    elif row["stage"] != SYNCED:
        # Changed again elsewhere since, which is the newer word
        store.update_card_change(row["id"], stage=SYNCED, synced_at=now)


def settle(store, notes, pending_graves, now):
    """Moves changes along from what a fresh read of the collection says."""
    by_id = {note["nid"]: note for note in notes}
    for row in live(store.card_changes(open_only=True)):
        note = by_id.get(row["nid"])
        if row["op"] == DELETE:
            if note is not None:
                store.update_card_change(row["id"], problem=UNDONE, reader_front=note.get("raw_front"),
                                         reader_back=note.get("raw_back"))
            elif row["stage"] == APPLIED and row["nid"] not in pending_graves:
                store.update_card_change(row["id"], stage=SYNCED, synced_at=now, on_ankiweb=1)
            continue
        if note is None:
            if row["stage"] == APPLIED:
                store.update_card_change(row["id"], problem="gone")
            continue
        if row["op"] == EDIT:
            _settled_edit(store, row, note, now)
        elif row["op"] == SUSPEND:
            if not note.get("suspended"):
                store.update_card_change(row["id"], problem=UNDONE)
            elif note.get("card_usn") != -1 and row["stage"] == APPLIED:
                store.update_card_change(row["id"], stage=SYNCED, synced_at=now, on_ankiweb=1)
        elif row["op"] == UNSUSPEND and row["stage"] == APPLIED:
            if note.get("suspended"):
                store.update_card_change(row["id"], stage=SYNCED, synced_at=now)
            elif note.get("card_usn") != -1:
                store.update_card_change(row["id"], stage=SYNCED, synced_at=now, on_ankiweb=1)


# The Changes tab


def _plural(n, word):
    return f"{n} {word}{'' if n == 1 else 's'}"


def _steps(stage):
    done = STAGES.index(stage)
    return [{"label": label, "cls": "trk now" if i == done else "trk done" if i < done else "trk",
             "dot": "dot" if i <= done else "dot off"} for i, label in enumerate(_STEP_NAMES)]


def _shown_text(row, note):
    """Question and answer as they were, and as they are now, in plain text."""
    old_front, old_back = _plain(row["expect_front"]), _plain(row["expect_back"])
    if row["expect_front"] is None and note:
        old_front, old_back = note.get("front", ""), note.get("back", "")
    front = _plain(to_raw(row["front"])) if row["front"] is not None else old_front
    back = _plain(to_raw(row["back"])) if row["back"] is not None else old_back
    return old_front, old_back, front, back


def _item(row, note):
    old_front, old_back, front, back = _shown_text(row, note)
    kinds = {EDIT: "Reworded", DELETE: "Deleted for good", UNSUSPEND: "Unsuspended", SUSPEND: "Suspended"}
    return {
        "id": row["id"], "nid": row["nid"], "op": row["op"], "deck": row["deck"], "kind": kinds[row["op"]],
        "front": front, "oldFront": old_front, "frontChanged": row["front"] is not None,
        "back": back, "oldBack": old_back, "backChanged": row["back"] is not None,
        "learn": bool(row["learn"]) and row["op"] == EDIT,
        "steps": _steps(row["stage"]), "now": _STEP_NOW[STAGES.index(row["stage"])],
        "canUndo": can_undo(row),
    }


def _need(row, note):
    old_front, old_back, front, back = _shown_text(row, note)
    problem = row["problem"]
    now_front = _plain(row["reader_front"]) if row["reader_front"] is not None else (note or {}).get("front", old_front)
    now_back = _plain(row["reader_back"]) if row["reader_back"] is not None else (note or {}).get("back", old_back)
    item = {"id": row["id"], "deck": row["deck"], "tag": "UNDONE", "hasCompare": False, "mine": front, "theirs": "",
            "theirsLabel": "Now in your collection", "showMine": False, "mineLabel": "", "dropLabel": "OK", "title": ""}
    answer_only = row["op"] == EDIT and row["front"] is None
    if problem == UNDONE:
        titles = {EDIT: "This edit was made, then a sync with AnkiWeb put the old text back.",
                  SUSPEND: "This card was suspended, then a change elsewhere put it back in your reviews.",
                  DELETE: "This card was deleted, then a sync with AnkiWeb brought it back.",
                  UNSUSPEND: "This card was unsuspended, then a change elsewhere suspended it again."}
        item.update(tag="UNDONE BY A SYNC" if row["op"] == EDIT else "UNDONE", title=titles[row["op"]],
                    hasCompare=row["op"] == EDIT, mine=back if answer_only else front,
                    theirs=now_back if answer_only else now_front, showMine=True, mineLabel="Apply again",
                    dropLabel="Leave it")
    else:
        item.update(tag="CARD IS GONE", hasCompare=True, theirs="No such card", theirsLabel="In your collection",
                    title="This card was deleted in Anki or on another device. "
                          "Your text is below in case you want it for a new card.")
    return item


def view(store, notes, syncing=False):
    """The Changes tab, and what other screens say about changes."""
    rows = store.card_changes()
    by_id = {note["nid"]: note for note in notes}
    open_rows = [row for row in rows if row["closed_at"] is None]
    needs = [row for row in open_rows if row["problem"]]
    fine = [row for row in open_rows if not row["problem"]]
    moving = [row for row in fine if row["op"] != SUSPEND and row["stage"] != SYNCED]
    suspended = [row for row in fine if row["op"] == SUSPEND]
    done = [row for row in fine if row["op"] != SUSPEND and row["stage"] == SYNCED]
    waiting = sum(row["stage"] == APPLIED for row in fine)
    error = store.get_setting(ERROR_SETTING) if waiting else None

    banner = None
    if error:
        banner = {"kind": "error", "text": error}
    elif syncing:
        banner = {"kind": "wait", "title": "Syncing with AnkiWeb...", "text": "This usually takes a few seconds."}
    elif waiting:
        banner = {"kind": "wait", "title": f"{_plural(waiting, 'change')} not on AnkiWeb yet",
                  "text": "They are in your collection here, and go to AnkiWeb with the next sync."}

    return {
        "banner": banner,
        "waiting": waiting,
        "needs": [_need(row, by_id.get(row["nid"])) for row in needs],
        "moving": [_item(row, by_id.get(row["nid"])) for row in moving],
        "suspended": [_item(row, by_id.get(row["nid"])) for row in suspended],
        "done": [{"id": row["id"], "front": _shown_text(row, by_id.get(row["nid"]))[2],
                  "tag": "DELETED" if row["op"] == DELETE and row["on_ankiweb"] else "ON ANKIWEB" if row["on_ankiweb"] else "DONE"}
                 for row in done[-20:]],
        "open": len(needs) + len(moving) + sum(row["stage"] != SYNCED for row in suspended),
        "saveHint": "Goes into your collection now, and to AnkiWeb with the next sync.",
    }


def card_status(rows, nid):
    """What the Clashes tab says about a card's change."""
    found = None
    for row in rows:
        if row["nid"] == nid and row["closed_at"] is None and not (row["op"] == EDIT and row["stage"] == SYNCED):
            found = row
    if found is None:
        return None
    away = found["op"] in (SUSPEND, DELETE)
    if found["problem"]:
        tag = ("tg bad", "UNDONE")
    elif found["op"] == UNSUSPEND:
        tag = ("tg onr", "UNSUSPENDED")
    elif found["stage"] == APPLIED:
        tag = ("tg onr", "SUSPENDED, NOT SYNCED YET" if away else "CHANGED, NOT SYNCED YET")
    else:
        tag = ("tg done", "SUSPENDED" if away else "DONE")
    return {"row": found, "tagCls": tag[0], "tagText": tag[1], "away": away and not found["problem"],
            "canUndo": can_undo(found), "isEdit": found["op"] == EDIT and not found["problem"]}
