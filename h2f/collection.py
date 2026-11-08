"""The app's own copy of the Anki collection, kept in step with AnkiWeb.

Every call takes one lock, because an Anki collection must not be used from two threads
at once. Nothing here ever uploads a whole collection to AnkiWeb.
"""

import re
import threading

from . import changes, keychain, paths

TAG = "highlights-to-flashcards"
PBANKI_NOTETYPE = "pbAnki KOReader"
OWN_NOTETYPE = "Highlights to Flashcards"
CLOZE_SUFFIX = " Cloze"
PBANKI_DECK = "KOReader"
PBANKI_TAG = "koreader"
DEFAULT_DECK = "Highlights"
CONTEXT_FIELDS = ("Quote", "Book", "Author", "Chapter", "Page", "File", "Position")
EDITABLE_NOTETYPES = ("Basic", "Cloze", PBANKI_NOTETYPE, PBANKI_NOTETYPE + CLOZE_SUFFIX,
                      OWN_NOTETYPE, OWN_NOTETYPE + CLOZE_SUFFIX)
_CLOZE = re.compile(r"\{\{c\d+::")

NOT_LOGGED_IN = "Log in to AnkiWeb in Settings first."
FULL_UPLOAD = ("AnkiWeb wants this computer's copy to replace everything there. This app never does that. "
               "Sync from Anki on your phone or computer, then try again.")
FULL_SYNC_LOCAL = ("AnkiWeb wants a full sync, which would lose the cards saved here that it doesn't have yet. "
                   "Nothing was changed. Sync from Anki on your phone or computer first, then try again.")


class CollectionError(Exception):
    """A plain sentence for the screen."""


def escape_html(text):
    return (text or "").replace("&", "&amp;").replace("<", "&lt;").replace(">", "&gt;")


def text_to_html(text):
    return escape_html((text or "").strip()).replace("\n", "<br>")


def deck_name(parent, title):
    title = " ".join((title or "").replace("::", ":").split())
    return f"{parent}::{title}" if title else parent


def _plain_error(e):
    text = str(e).strip()
    if "password" in text.lower() or "auth" in text.lower():
        return "AnkiWeb didn't accept that email and password."
    if "network" in text.lower() or "connect" in text.lower() or "timed out" in text.lower():
        return "Couldn't reach AnkiWeb. Check the internet connection."
    return "AnkiWeb sync failed: " + (text.splitlines()[0][:200] if text else "no reason given") + "."


class Collection:
    def __init__(self, path=None):
        self.path = path or paths.collection_path()
        self.lock = threading.RLock()
        self._col = None

    # Opening

    def _open(self):
        if self._col is None:
            from anki.collection import Collection as AnkiCollection

            self.path.parent.mkdir(parents=True, exist_ok=True)
            self._col = AnkiCollection(str(self.path))
        return self._col

    def close(self):
        with self.lock:
            if self._col is not None:
                self._col.close()
                self._col = None

    # AnkiWeb

    def _auth(self):
        from anki.sync import SyncAuth

        stored = keychain.get(keychain.ANKIWEB)
        if not stored or not stored.get("hkey"):
            return None
        return SyncAuth(hkey=stored["hkey"], endpoint=stored.get("endpoint") or None)

    def logged_in(self):
        return self._auth() is not None

    def login(self, email, password):
        """Swaps the email and password for a sync key, kept in the Keychain. The password isn't kept."""
        with self.lock:
            try:
                auth = self._open().sync_login(email, password, None)
            except Exception as e:
                raise CollectionError(_plain_error(e)) from None
        keychain.put(keychain.ANKIWEB, {"hkey": auth.hkey, "endpoint": auth.endpoint or ""})

    def logout(self):
        keychain.forget(keychain.ANKIWEB)

    def unsynced(self):
        """How many notes, cards, review entries and deletions AnkiWeb hasn't seen yet."""
        with self.lock:
            db = self._open().db
            return sum(db.scalar(f"SELECT COUNT() FROM {table} WHERE usn = -1")
                       for table in ("notes", "cards", "revlog", "graves"))

    def _is_empty(self):
        return self._open().db.scalar("SELECT COUNT() FROM notes") == 0

    def sync(self):
        """Syncs with AnkiWeb. Returns "synced" or "downloaded". Raises CollectionError."""
        from anki.sync_pb2 import SyncCollectionResponse

        required = SyncCollectionResponse.ChangesRequired
        auth = self._auth()
        if auth is None:
            raise CollectionError(NOT_LOGGED_IN)
        with self.lock:
            col = self._open()
            try:
                out = col.sync_collection(auth, False)
            except Exception as e:
                raise CollectionError(_plain_error(e)) from None
            if out.new_endpoint:
                keychain.put(keychain.ANKIWEB, {"hkey": auth.hkey, "endpoint": out.new_endpoint})
                auth.endpoint = out.new_endpoint
            if out.required in (required.NO_CHANGES, required.NORMAL_SYNC):
                return "synced"
            if out.required == required.FULL_UPLOAD:
                raise CollectionError(FULL_UPLOAD)
            if out.required == required.FULL_SYNC and not self._is_empty() and self.unsynced():
                raise CollectionError(FULL_SYNC_LOCAL)
            col.close_for_full_sync()
            try:
                col.full_upload_or_download(auth=auth, server_usn=None, upload=False)
            except Exception as e:
                raise CollectionError(_plain_error(e)) from None
            finally:
                col.reopen(after_full_sync=True)
            return "downloaded"

    # Reading

    def notes(self):
        """(notes, ids of notes deleted but not yet synced), in the shape clashes.py uses."""
        with self.lock:
            db = self._open().db
            decks = {row[0]: row[1].split("\x1f")[-1] for row in db.all("SELECT id, name FROM decks")}
            types = dict(db.all("SELECT id, name FROM notetypes"))
            graves = {row[0] for row in db.all("SELECT oid FROM graves WHERE type = 1 AND usn = -1")}
            rows = db.all(
                "SELECT n.id, n.flds, MIN(c.did), n.mid, n.usn, n.tags, COUNT(c.id), MIN(c.usn), MAX(c.reps), "
                "MAX(c.lapses), MAX(c.ivl), MIN(c.queue = -1) FROM notes n JOIN cards c ON c.nid = n.id "
                "GROUP BY n.id ORDER BY n.id"
            )
        notes = []
        for nid, fields, deck, mid, usn, tags, cards, card_usn, reps, lapses, ivl, suspended in rows:
            parts = fields.split("\x1f")
            front = changes.plain(parts[0])
            if front:
                raw_back = parts[1] if len(parts) > 1 else ""
                notes.append({"nid": nid, "deck": decks.get(deck, ""), "front": front, "back": changes.plain(raw_back),
                              "raw_front": parts[0], "raw_back": raw_back, "notetype": types.get(mid),
                              "fields": len(parts), "cards": cards, "usn": usn, "card_usn": card_usn, "reps": reps,
                              "lapses": lapses, "ivl": max(ivl, 0), "suspended": suspended, "tags": tags.strip()})
        return notes, graves

    # Adding

    def defaults(self):
        """(note type, deck parent, tags) to use when settings leave them blank."""
        with self.lock:
            if self._open().models.by_name(PBANKI_NOTETYPE):
                return PBANKI_NOTETYPE, PBANKI_DECK, [PBANKI_TAG, TAG]
        return OWN_NOTETYPE, DEFAULT_DECK, [TAG]

    def _notetype(self, name, cloze):
        col = self._open()
        wanted = name + (CLOZE_SUFFIX if cloze else "")
        found = col.models.by_name(wanted)
        if found:
            return found
        base = col.models.by_name("Cloze" if cloze else "Basic")
        if base is None:
            kind = 1 if cloze else 0
            base = next((m for m in col.models.all() if m.get("type") == kind and len(m["flds"]) >= 2), None)
        if base is None:
            raise CollectionError(f"The collection has no {'Cloze' if cloze else 'Basic'} note type to copy.")
        model = col.models.copy(base, add=False)
        model["name"] = wanted
        model["flds"] = model["flds"][:2]
        for field in CONTEXT_FIELDS:
            model["flds"].append(col.models.new_field(field))
        for ord_, field in enumerate(model["flds"]):
            field["ord"] = ord_
        col.models.add_dict(model)
        return col.models.by_name(wanted)

    def add_cards(self, cards, notetype=None, deck_parent=None, tags=None):
        """Adds every card as one undoable step. `cards` are dicts with front, back, quote, book,
        author, chapter, page, file, position. Returns the new note ids, in order."""
        default_type, default_parent, default_tags = self.defaults()
        notetype = notetype or default_type
        deck_parent = deck_parent or default_parent
        tags = tags or default_tags
        with self.lock:
            col = self._open()
            ids = []
            for card in cards:
                model = self._notetype(notetype, bool(_CLOZE.search(card.get("front") or "")))
                note = col.new_note(model)
                values = [text_to_html(card.get("front")), text_to_html(card.get("back")),
                          text_to_html(card.get("quote")), escape_html(card.get("book")),
                          escape_html(card.get("author")), escape_html(card.get("chapter")),
                          "" if card.get("page") in (None, "") else str(card["page"]),
                          escape_html(card.get("file")), escape_html(card.get("position"))]
                for i, value in enumerate(values[:len(note.fields)]):
                    note.fields[i] = value
                note.tags = list(tags)
                deck_id = col.decks.id(deck_name(deck_parent, card.get("book")))
                col.add_note(note, deck_id)
                ids.append(note.id)
            return ids

    def has_notes(self, note_ids):
        with self.lock:
            db = self._open().db
            return {nid for nid in note_ids if db.scalar("SELECT 1 FROM notes WHERE id = ?", nid)}

    # Changing

    def _card_ids(self, nid, extra=""):
        return list(self._open().find_cards(f"nid:{nid} {extra}".strip()))

    def _note(self, nid):
        from anki.errors import NotFoundError

        try:
            return self._open().get_note(nid)
        except NotFoundError:
            return None

    @staticmethod
    def _as_expected(note, expected_front, expected_back):
        fields = note.fields
        return (expected_front is None or fields[0] == expected_front) and \
            (expected_back is None or (fields[1] if len(fields) > 1 else "") == expected_back)

    def apply(self, op, nid, expected_front=None, expected_back=None, front=None, back=None, learn=False):
        """Makes one change. Returns "applied", "already" or a refusal: changed, gone,
        note_type, several_cards, formatted, empty, cloze."""
        with self.lock:
            col = self._open()
            note = self._note(nid)
            if note is None:
                return "already" if op == "delete" else "gone"
            if op == "suspend":
                active = self._card_ids(nid, "-is:suspended")
                if not active:
                    return "already"
                if not self._as_expected(note, expected_front, expected_back):
                    return "changed"
                col.sched.suspend_cards(active)
                return "applied"
            if op == "unsuspend":
                suspended = self._card_ids(nid, "is:suspended")
                if not suspended:
                    return "already"
                col.sched.unsuspend_cards(suspended)
                return "applied"
            if op == "delete":
                if not self._as_expected(note, expected_front, expected_back):
                    return "changed"
                col.remove_notes([nid])
                return "applied"
            if op != "edit":
                raise ValueError(op)
            target_front = None if front is None else changes.to_raw(front)
            target_back = None if back is None else changes.to_raw(back)
            current_back = note.fields[1] if len(note.fields) > 1 else ""
            at_target = (target_front is not None or target_back is not None) \
                and (target_front is None or target_front == note.fields[0]) \
                and (target_back is None or target_back == current_back)
            if at_target:
                if learn:
                    col.sched.schedule_cards_as_new(self._card_ids(nid), restore_position=True, reset_counts=False)
                return "already"
            if not self._as_expected(note, expected_front, expected_back):
                return "changed"
            model = note.note_type()
            if model is None or model["name"] not in EDITABLE_NOTETYPES or len(note.fields) < 2:
                return "note_type"
            if len(self._card_ids(nid)) != 1:
                return "several_cards"
            if (target_front is not None and changes.has_formatting(note.fields[0])) or \
                    (target_back is not None and changes.has_formatting(current_back)):
                return "formatted"
            if target_front == "":
                return "empty"
            if model.get("type") == 1 and target_front is not None and \
                    changes.cloze_numbers(target_front) != changes.cloze_numbers(note.fields[0]):
                return "cloze"
            if target_front is not None:
                note.fields[0] = target_front
            if target_back is not None:
                note.fields[1] = target_back
            col.update_note(note)
            if learn:
                col.sched.schedule_cards_as_new(self._card_ids(nid), restore_position=True, reset_counts=False)
            return "applied"
