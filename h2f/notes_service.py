"""Write notes: the slip-box side of the app.

The reader thinks about the book first, the AI gathers the highlights into ideas, and for each
idea the reader picks highlights, a title and related notes. The app creates the note, opens it
in Obsidian, keeps related notes fresh while they write, and links back from every note they
linked to. The AI never writes a note.
"""

import logging
import threading
import time
from functools import wraps

from . import ai, companion, epub, paths, prompts, store as store_module
from . import notes as notes_module
from . import notes_ai
from .store import NOTE_DISMISSED, NOTE_STATES, NOTE_TODO, NOTE_WRITTEN, hid_for
from .service import ServiceError, _pick_folder, _tilde

_log = logging.getLogger(__name__)

TOPIC = "notes"
JOBS = ("ideas", "links", "titles")
DEFAULT_MODELS = {"ideas": "claude-opus-5-5", "links": "claude-opus-5-5", "titles": "claude-sonnet-5-5"}
JOB_NOTES = {
    "ideas": "Reads the book, all highlights, margin notes and cards. Once per book.",
    "links": "Reads every note in the slip-box. Once per idea, and when you pause while writing.",
    "titles": "Short titles in the style of yours. Never the note itself.",
}
GATHER_TIMEOUT = 900
LINKS_TIMEOUT = 600
TITLES_TIMEOUT = 300
VAULT_CACHE_SECONDS = 3
CHOOSE_VAULT_SCRIPT = 'POSIX path of (choose folder with prompt "Where is your Obsidian slip-box?")'
QUESTIONS = [
    "How would you say this to a friend, without the book's words?",
    "Where else is this true, beyond this book?",
    "Which of your notes does this contradict, or make you doubt?",
    "What changes if this is true? What would you do differently?",
]
GATHERING = "gathering"
READY = "ready"
FAILED = "failed"
_NO_VAULT = "Pick your slip-box folder first."


def _book_of(note):
    return next((t for t in note["tags"] if t.startswith("#book/")), None)


def _route(method):
    @wraps(method)
    def wrapper(*args, **kwargs):
        try:
            return method(*args, **kwargs)
        except ServiceError:
            raise
        except notes_module.VaultError as e:
            raise ServiceError(409, str(e)) from None
        except store_module.NotFound:
            raise ServiceError(404, "That isn't on this computer any more.") from None
        except prompts.PromptNotFound as e:
            raise ServiceError(404, str(e)) from None
    return wrapper


class NotesService:
    def __init__(self, service, vault_factory=None):
        self._s = service
        self._store = service._store
        self._vault_factory = vault_factory or (lambda root: notes_module.Vault(root, launch=service._launch))
        self._jobs = {}
        self._lock = threading.Lock()
        self._closing = False
        self._cache = {"at": 0, "root": None, "notes": None}

    # Settings

    def _settings(self):
        settings = self._store.get_settings()
        models = {**DEFAULT_MODELS, **(settings.get("notes_models") or {})}
        return {"vault": settings.get("notes_vault") or "", "models": models,
                "prompt": settings.get("notes_prompt") or prompts.STANDARD}

    def _vault(self, required=True):
        root = self._settings()["vault"]
        if not root:
            if required:
                raise ServiceError(409, _NO_VAULT)
            return None
        vault = self._vault_factory(root)
        if required and not vault.root.is_dir():
            raise ServiceError(409, "The slip-box folder isn't there any more. Pick it again before you start.")
        return vault

    def _notes(self, vault, fresh=False):
        now = time.time()
        cache = self._cache
        if fresh or cache["root"] != str(vault.root) or now - cache["at"] > VAULT_CACHE_SECONDS:
            cache.update(at=now, root=str(vault.root), notes=vault.notes())
        return cache["notes"]

    def _model_view(self, model_id):
        try:
            return {"id": model_id, "label": ai.model_info(model_id)["label"]}
        except ai.AIError:
            return {"id": model_id, "label": model_id}

    @_route
    def setup(self):
        """The settings screen before a book: the vault, the AI for each job and the prompts."""
        settings = self._settings()
        vault = self._vault(required=False)
        check = vault.check() if vault else {"ok": False, "notes": 0, "advanced_uri": False, "git": False,
                                             "problem": None}
        usable = self._s._usable_models()
        files = []
        for row in prompts.notes_files(settings["prompt"]):
            files.append({"kind": row["kind"], "label": row["label"], "file": row["file"],
                          "status": row["status"], "display": _tilde(row["own_path"]),
                          "text": row["text"]})
        return {
            "vault": {"path": settings["vault"], "display": _tilde(settings["vault"]) if settings["vault"] else "",
                      "name": vault.name if vault else "", **check},
            "jobs": [{"id": job, "label": next(k["label"] for k in prompts.NOTE_KINDS if k["key"] == f"notes-{job}"),
                      "model": settings["models"][job], "note": JOB_NOTES[job]} for job in JOBS],
            "models": [{"id": m["id"], "label": m["label"], "a": m["assistant"]} for m in usable],
            "assistants": [{"id": a["id"], "name": a["name"], "found": a["found"]} for a in self._s._assistants()],
            "prompt": settings["prompt"],
            "prompt_names": prompts.folder_names(),
            "files": files,
            "reloadAgo": self._s._prompt_watcher.seconds_since_reload(),
        }

    @_route
    def update_settings(self, changes):
        settings = self._settings()
        new = {}
        if "vault" in changes:
            vault = notes_module.Vault(changes["vault"].strip())
            if not str(vault.root).startswith("/"):
                raise ServiceError(400, "The slip-box has to be a full path.")
            check = vault.check()
            if not check["ok"]:
                raise ServiceError(400, check["problem"])
            new["notes_vault"] = str(vault.root).rstrip("/")
        if "models" in changes:
            models = dict(settings["models"])
            for job, model_id in changes["models"].items():
                if job not in JOBS or not isinstance(model_id, str):
                    raise ServiceError(400, "The app doesn't know that AI job.")
                self._s._check_model(model_id)
                models[job] = model_id
            new["notes_models"] = models
        if "prompt" in changes:
            new["notes_prompt"] = prompts.check_name(changes["prompt"])
        self._store.set_settings(new)
        self._publish(None, "settings")
        return self.setup()

    @_route
    def choose_vault(self):
        picked = _pick_folder(CHOOSE_VAULT_SCRIPT)
        if not picked:
            return {"folder": None, **self.setup()}
        return {"folder": picked, **self.update_settings({"vault": picked})}

    # Books

    def _book(self, book_id):
        book = self._store.get_book(book_id)
        if book is None:
            self._s.books()
            book = self._s._book(book_id)
        self._s._refresh_book(book["device_path"])
        return self._s._book(book_id)

    def _highlights(self, book):
        rows = self._s._highlights(book)
        for h in rows:
            h["chapter"] = h.get("chapter_name") or h.get("chapter") or ""
        return rows

    def _cards_by_key(self, book, highlights):
        by_hid = {h["hid"]: h["key"] for h in highlights}
        cards = {}
        for card in self._store.list_saved_cards(book["id"]):
            key = by_hid.get(card["hid"])
            if key:
                cards.setdefault(key, []).append({"front": card["front"], "back": card["back"]})
        return cards

    def _record(self, book):
        record = self._store.notes_book(book["id"])
        if record is None or not record["tag"]:
            record = self._store.set_notes_book(book["id"], tag=notes_module.book_tag(book["title"]))
        return record

    def _publish(self, book_id, kind, **fields):
        topic = f"{TOPIC}:{book_id}" if book_id else TOPIC
        self._s._hub.publish({"topic": topic, "type": kind, "book_id": book_id, **fields})

    def _job_view(self, kind, target):
        with self._lock:
            return (kind, target) in self._jobs

    @_route
    def book_view(self, book_id):
        """Everything the book's screens show."""
        book = self._book(book_id)
        record = self._record(book)
        highlights = self._highlights(book)
        by_key = {h["key"]: h for h in highlights}
        vault = self._vault(required=False)
        vault_notes = self._notes(vault) if vault and vault.root.is_dir() else []
        titles = {n["title"].lower(): n for n in vault_notes}
        written = self._store.notes_written(book_id)
        ideas = self._store.note_ideas(book_id)
        cards = self._cards_by_key(book, highlights)

        def idea_view(idea):
            keys = [k for k in idea["keys"] if k in by_key]
            flags = [f for f in idea["flags"] if f["note"].lower() in titles]
            talks = [r["note"] for r in idea["related"] or [] if r["note"].lower() in titles]
            talks += [f["note"] for f in flags if f["note"] not in talks]
            mine = [by_key[k]["note"] for k in keys if by_key[k].get("note")]
            return {
                "id": idea["id"], "origin": idea["origin"], "title": idea["title"], "summary": idea["summary"],
                "chapter": idea["chapter"], "count": len(keys),
                "quote": idea["quote"] or (by_key[keys[0]]["text"] if keys else ""),
                "cards": sum(len(cards.get(k, [])) for k in keys),
                "mine": mine[:3],
                "chapters": list(dict.fromkeys(by_key[k]["chapter"] for k in keys if by_key[k]["chapter"])),
                "flags": flags, "talks": talks,
                "state": idea["state"], "note_id": idea["note_id"],
                "note": next((w["title"] for w in written if w["id"] == idea["note_id"]), None),
            }

        other_books, links = set(), 0
        for note in written:
            links += len(note["linked"])
            for title in note["linked"]:
                linked = titles.get(title.lower())
                for tag in linked["tags"] if linked else []:
                    if tag.startswith("#book/") and tag.lower() != record["tag"].lower():
                        other_books.add(tag.lower())
        main = [idea_view(i) for i in ideas if i["origin"] == "highlights"]
        missed = [idea_view(i) for i in ideas if i["origin"] == "missed"]
        hub = notes_module.hub_title(book["title"])
        return {
            "id": book_id,
            "title": book["title"],
            "author": book["author"],
            "tag": record["tag"],
            "reflection": record["reflection"],
            "status": GATHERING if self._job_view("ideas", book_id) else (record["status"] or ""),
            "error": record["error"],
            "gathered_at": record["gathered_at"],
            "highlight_count": len(highlights),
            "card_count": sum(len(c) for c in self._cards_by_key(book, highlights).values()),
            "companion": bool(companion.find(book["data"].get("epub")).get("found")),
            "vault": {"ok": bool(vault and vault.root.is_dir()), "notes": len(vault_notes),
                      "tagged": sum(1 for n in vault_notes if record["tag"].lower() in [t.lower() for t in n["tags"]])},
            "ideas": main,
            "missed": missed,
            "written": [self._written_view(w, titles, record["tag"]) for w in written],
            "stats": {"notes": len(written), "links": links, "books": len(other_books),
                      "todo": sum(1 for i in main if i["state"] == NOTE_TODO)},
            "hub": {"title": hub, "exists": hub.lower() in titles,
                    "preview": self._hub_text(book, record, written, vault, titles)},
        }

    def _written_view(self, note, titles, tag):
        linked = []
        for title in note["linked"]:
            found = titles.get(title.lower())
            books = [t for t in (found["tags"] if found else []) if t.startswith("#book/")]
            linked.append({"title": title, "book": books[0] if books else None,
                           "same_book": any(b.lower() == tag.lower() for b in books)})
        return {"id": note["id"], "title": note["title"], "path": note["path"], "idea_id": note["idea_id"],
                "added": note["added"], "linked": linked, "done": bool(note["done_at"]),
                "exists": note["title"].lower() in titles}

    @_route
    def update_book(self, book_id, changes):
        self._s._book(book_id)
        fields = {}
        if "tag" in changes:
            tag = notes_module.clean_tag(changes["tag"])
            if not tag:
                raise ServiceError(400, "A tag is a # followed by letters, numbers, / or -.")
            fields["tag"] = tag
        if "reflection" in changes:
            fields["reflection"] = changes["reflection"]
        self._store.set_notes_book(book_id, **fields)
        self._publish(book_id, "book")
        return {"ok": True}

    # Jobs

    def _start(self, kind, target, book_id, work, **fields):
        key = (kind, target)
        token = ai.CancelToken()

        def body():
            outcome = {}
            try:
                work(token)
            except ai.AICancelled:
                outcome = {"error": None}
            except (ai.AIError, notes_module.VaultError, ServiceError) as e:
                outcome = {"error": getattr(e, "sentence", None) or str(e)}
            except Exception:
                _log.exception("A notes %s request failed", kind)
                outcome = {"error": "Something went wrong. Your notes are safe."}
            finally:
                with self._lock:
                    self._jobs.pop(key, None)
            try:
                if outcome.get("error") and kind == "ideas":
                    self._store.set_notes_book(book_id, status=FAILED, error=outcome["error"])
                self._publish(book_id, kind, target=target, **fields, **outcome)
            finally:
                self._s.release_thread()

        with self._lock:
            if self._closing:
                raise ServiceError(503, "The app is closing.")
            if key in self._jobs:
                return False
            thread = threading.Thread(target=body, name=f"h2f-notes-{kind}", daemon=True)
            self._jobs[key] = {"token": token, "thread": thread}
        thread.start()
        self._publish(book_id, kind, target=target, started=True, **fields)
        return True

    def stop(self, wait=5):
        with self._lock:
            self._closing = True
            jobs = list(self._jobs.values())
        for job in jobs:
            job["token"].cancel()
        for job in jobs:
            job["thread"].join(wait)

    def _ask(self, job, messages, token, timeout):
        model = self._settings()["models"][job]
        self._s._check_model(model)
        query = self._s._query or ai.query
        return query(model, messages, timeout=timeout, cancel=token)

    def _prompt(self, kind):
        return prompts.notes_effective(self._settings()["prompt"])[f"notes-{kind}"]

    # Gathering ideas

    @staticmethod
    def _book_text(book):
        path = book["data"].get("epub")
        if not path:
            return None
        try:
            return epub.open_epub(path).book_text()
        except (epub.EpubError, OSError):
            _log.warning("could not read the book at %s", path, exc_info=True)
            return None

    @_route
    def gather(self, book_id):
        """Starts the AI gathering the book's highlights into ideas."""
        book = self._book(book_id)
        vault = self._vault()
        self._record(book)

        def work(token):
            record = self._store.notes_book(book_id)
            highlights = self._highlights(book)
            vault_notes = self._notes(vault, fresh=True)
            by_key = self._cards_by_key(book, highlights)
            cards = [card for h in highlights for card in by_key.get(h["key"], [])]
            found = companion.find(book["data"].get("epub"))
            companion_text = companion.context_for_book(companion.read_text(found), 1)
            known = self._missed_from_cards(book_id)
            hub = notes_module.hub_title(book["title"]).lower()
            messages = notes_ai.build_gather(
                {"title": book["title"], "author": book["author"]}, highlights, cards, companion_text,
                [n for n in vault_notes if n["title"].lower() != hub], record["tag"], self._prompt("ideas"), known,
                self._book_text(book))
            reply = self._ask("ideas", messages, token, GATHER_TIMEOUT)
            parsed, problem = notes_ai.parse_gather(reply, len(highlights), [n["title"] for n in vault_notes],
                                                    len(known))
            if problem:
                raise notes_module.VaultError(problem)
            ideas = [{**idea, "keys": [highlights[n - 1]["key"] for n in idea["highlights"]]}
                     for idea in parsed["ideas"]]
            if known:
                answers = {m["number"]: m for m in parsed["missed"]}
                missed = [{**m, "summary": answers.get(n, {}).get("summary") or m["summary"],
                           "flags": answers.get(n, {}).get("flags", [])} for n, m in enumerate(known, 1)]
            else:
                missed = [{"title": m["title"], "summary": m["why"], "chapter": m["chapter"], "quote": m["quote"],
                           "flags": m["flags"]} for m in parsed["missed"]]
            with self._store.transaction():
                self._store.replace_note_ideas(book_id, "highlights", ideas)
                self._store.replace_note_ideas(book_id, "missed", missed)
                self._store.set_notes_book(book_id, status=READY, error=None, gathered_at=time.time())

        self._store.set_notes_book(book_id, status=GATHERING, error=None)
        self._start("ideas", book_id, book_id, work)
        return {"started": True}

    def _missed_from_cards(self, book_id):
        # Key ideas the card side already found in the Book Companion
        found = self._store.list_ideas(book_id, states=(store_module.DRAFT, store_module.SAVED, store_module.REMOVED))
        return [{"title": i["title"], "summary": i["note"], "chapter": i["chapter"], "quote": i["quote"]}
                for i in found if i["title"]]

    @_route
    def set_idea_state(self, idea_id, state):
        if state not in NOTE_STATES:
            raise ServiceError(400, "The app doesn't know that state.")
        idea = self._store.update_note_idea(idea_id, state=state)
        self._publish(idea["book_id"], "idea", target=idea_id)
        return {"ok": True}

    # One idea

    def _idea_parts(self, idea):
        book = self._book(idea["book_id"])
        highlights = self._highlights(book)
        by_key = {h["key"]: h for h in highlights}
        cards = self._cards_by_key(book, highlights)
        chosen = [by_key[k] for k in idea["keys"] if k in by_key]
        return book, highlights, chosen, cards

    @_route
    def idea_view(self, idea_id):
        idea = self._store.note_idea(idea_id)
        book, highlights, chosen, cards = self._idea_parts(idea)
        record = self._record(book)
        vault = self._vault(required=False)
        vault_notes = self._notes(vault) if vault and vault.root.is_dir() else []
        titles = {n["title"].lower() for n in vault_notes}
        books = {n["title"].lower(): _book_of(n) for n in vault_notes}
        related = [{**r, "book": books.get(r["note"].lower())} for r in idea["related"] or []
                   if r["note"].lower() in titles]
        for flag in idea["flags"]:
            if flag["note"].lower() in titles and flag["note"].lower() not in {r["note"].lower() for r in related}:
                related.append({"note": flag["note"], "kind": "disagrees" if flag["kind"] == "disagrees" else "same idea",
                                "why": flag["why"], "book": books.get(flag["note"].lower())})
        if vault and idea["related"] is None and not self._job_view("links", idea_id):
            self._links_for_idea(idea, book, chosen, cards)
        if vault and idea["titles"] is None and not self._job_view("titles", idea_id):
            self._titles_for_idea(idea, chosen)
        chosen_keys = set(idea["keys"])
        return {
            "id": idea["id"], "book_id": book["id"], "book": book["title"], "origin": idea["origin"],
            "title": idea["title"], "summary": idea["summary"], "chapter": idea["chapter"], "quote": idea["quote"],
            "state": idea["state"], "note_id": idea["note_id"], "tag": record["tag"],
            "flags": idea["flags"],
            "highlights": [{"key": h["key"], "text": h["text"], "note": h.get("note"), "chapter": h["chapter"],
                            "cards": cards.get(h["key"], []), "chosen": h["key"] in chosen_keys}
                           for h in highlights],
            "related": related,
            "related_busy": self._job_view("links", idea_id),
            "related_at": idea["related_at"],
            "titles": [t for t in idea["titles"] or [] if t.lower() not in titles],
            "titles_busy": self._job_view("titles", idea_id),
            "taken": sorted(n["title"] for n in vault_notes) if vault_notes else [],
            "questions": QUESTIONS,
            "vault_ok": bool(vault and vault.root.is_dir()),
        }

    @_route
    def idea_keys(self, idea_id, keys):
        idea = self._store.note_idea(idea_id)
        self._store.update_note_idea(idea_id, keys=[k for k in keys if isinstance(k, str)])
        self._publish(idea["book_id"], "idea", target=idea_id)
        return {"ok": True}

    def _links_for_idea(self, idea, book, chosen, cards):
        vault = self._vault()

        def work(token):
            vault_notes = self._notes(vault, fresh=True)
            skip = [notes_module.hub_title(book["title"])]
            asked = [f"{idea['title']} {idea.get('summary') or ''}"] + [h["text"] for h in chosen]
            messages = notes_ai.build_links(
                {"title": book["title"], "author": book["author"]}, idea, chosen,
                [c for h in chosen for c in cards.get(h["key"], [])], None,
                notes_ai.vault_index(vault_notes, skip=skip), self._prompt("links"),
                notes_ai.likely_notes(vault_notes, asked, per_text=10, limit=notes_ai.LINKS_FULL_NOTES, skip=skip))
            reply = self._ask("links", messages, token, LINKS_TIMEOUT)
            links, problem = notes_ai.parse_links(reply, [n["title"] for n in vault_notes], skip=skip)
            if problem:
                raise notes_module.VaultError(problem)
            self._store.update_note_idea(idea["id"], related=links, related_at=time.time())

        return self._start("links", idea["id"], book["id"], work)

    def _titles_for_idea(self, idea, chosen, draft=None):
        vault = self._vault()

        def work(token):
            vault_notes = self._notes(vault)
            names = [n["title"] for n in vault_notes]
            messages = notes_ai.build_titles(idea, chosen or [{"text": idea["quote"], "chapter": idea["chapter"]}],
                                             draft, names, self._prompt("titles"), seed=idea["id"])
            reply = self._ask("titles", messages, token, TITLES_TIMEOUT)
            titles, problem = notes_ai.parse_titles(reply, names)
            if problem:
                raise notes_module.VaultError(problem)
            self._store.update_note_idea(idea["id"], titles=titles)

        return self._start("titles", idea["id"], idea["book_id"], work)

    @_route
    def idea_related(self, idea_id):
        idea = self._store.note_idea(idea_id)
        book, _highlights, chosen, cards = self._idea_parts(idea)
        return {"started": self._links_for_idea(idea, book, chosen, cards)}

    @_route
    def idea_titles(self, idea_id):
        idea = self._store.note_idea(idea_id)
        _book, _highlights, chosen, _cards = self._idea_parts(idea)
        return {"started": self._titles_for_idea(idea, chosen)}

    def _quotes(self, idea, chosen):
        quotes = [h["text"] for h in chosen]
        if not quotes and idea["quote"]:
            quotes = [idea["quote"]]
        return quotes

    @_route
    def create_note(self, idea_id, title, link):
        """Writes the new note, links back from the notes ticked, and opens it in Obsidian."""
        idea = self._store.note_idea(idea_id)
        book, _highlights, chosen, _cards = self._idea_parts(idea)
        record = self._record(book)
        vault = self._vault()
        title = notes_module.clean_title(title)
        if not title:
            raise ServiceError(400, "The note needs a title.")
        targets = []
        hub = notes_module.hub_title(book["title"]).lower()
        for name in link:
            rel = vault.find(name) if isinstance(name, str) else None
            if rel and name.lower() not in (title.lower(), hub):
                targets.append((vault.full(rel).stem, rel))
        text = notes_module.new_note(record["tag"], self._quotes(idea, chosen), [t for t, _ in targets])
        rel = vault.create(title, text)
        note = self._store.add_note_written(book["id"], idea_id, title, rel, linked=[t for t, _ in targets])
        self._store.update_note_idea(idea_id, state=NOTE_WRITTEN, note_id=note["id"])
        problems = self._link_back(vault, title, [r for _, r in targets])
        self._cache["at"] = 0
        vault.open_note(rel, new=True)
        self._publish(book["id"], "written", target=note["id"])
        return {"note_id": note["id"], "problems": problems}

    @_route
    def add_to_note(self, idea_id, existing):
        """Adds the chosen highlights to an existing note, and opens it in Obsidian."""
        idea = self._store.note_idea(idea_id)
        book, _highlights, chosen, _cards = self._idea_parts(idea)
        vault = self._vault()
        rel = vault.find(existing or "")
        if not rel:
            raise ServiceError(404, "That note isn't in the slip-box.")
        before = vault.read(rel)
        vault.change(rel, before, notes_module.append_quotes(before, self._quotes(idea, chosen)))
        title = vault.full(rel).stem
        note = self._store.add_note_written(book["id"], idea_id, title, rel, added=True)
        self._store.update_note_idea(idea_id, state=NOTE_WRITTEN, note_id=note["id"])
        self._cache["at"] = 0
        vault.open_note(rel)
        self._publish(book["id"], "written", target=note["id"])
        return {"note_id": note["id"], "problems": []}

    def _link_back(self, vault, title, rels):
        problems = []
        for rel in rels:
            try:
                before = vault.read(rel)
                vault.change(rel, before, notes_module.add_link(before, title))
            except notes_module.VaultError as e:
                problems.append(str(e))
        return problems

    # Writing

    def _written_parts(self, note_id):
        note = self._store.note_written(note_id)
        vault = self._vault()
        rel = note["path"]
        try:
            text = vault.read(rel)
        except notes_module.VaultError:
            # Renamed in Obsidian: follow it by title when it can
            found = vault.find(note["title"])
            if not found:
                raise
            rel = found
            text = vault.read(rel)
            note = self._store.update_note_written(note_id, path=rel)
        return note, vault, rel, text

    @_route
    def written_view(self, note_id):
        note, vault, rel, text = self._written_parts(note_id)
        vault_notes = self._notes(vault)
        titles = {n["title"].lower(): n for n in vault_notes}
        idea = self._store.note_idea(note["idea_id"]) if note["idea_id"] else None
        book = self._s._book(note["book_id"])
        record = self._record(book)
        highlights = self._highlights(book)
        by_key = {h["key"]: h for h in highlights}
        typed = [t for t in notes_module.links_in(text) if t.lower() != note["title"].lower()]
        linked = [{"title": titles[t.lower()]["title"], "book": next(
            (tag for tag in titles[t.lower()]["tags"] if tag.startswith("#book/")), None)}
            for t in typed if t.lower() in titles]
        mtime = vault.full(rel).stat().st_mtime
        related = [{**r, "book": _book_of(titles[r["note"].lower()])}
                   for r in note["related"] or (idea["related"] if idea else None) or []
                   if r["note"].lower() in titles and r["note"].lower() not in {t.lower() for t in typed}
                   and r["note"].lower() != note["title"].lower()]
        seen = {r["note"].lower() for r in (idea["related"] if idea else None) or []}
        return {
            "id": note["id"], "book_id": note["book_id"], "title": note["title"], "path": rel,
            "added": note["added"], "done": bool(note["done_at"]), "saved_at": mtime,
            "text": text, "words": len(text.split()),
            "linked": linked, "missing": [t for t in typed if t.lower() not in titles],
            "related": [{**r, "new": note["related"] is not None and r["note"].lower() not in seen} for r in related],
            "related_busy": self._job_view("links", note_id),
            "related_at": note["related_at"] or (idea["related_at"] if idea else None),
            "related_for": note["related_for"],
            "highlights": [{"text": by_key[k]["text"], "note": by_key[k].get("note")}
                           for k in (idea["keys"] if idea else []) if k in by_key]
            or ([{"text": idea["quote"], "note": None}] if idea and idea["quote"] else []),
            "questions": QUESTIONS,
            "other_books": len({lk["book"].lower() for lk in linked if lk["book"]} - {record["tag"].lower()}),
        }

    @_route
    def written_related(self, note_id):
        """Looks again for related notes, reading what the reader has written so far."""
        note, vault, rel, text = self._written_parts(note_id)
        idea = self._store.note_idea(note["idea_id"]) if note["idea_id"] else {
            "title": note["title"], "summary": "", "keys": [], "quote": ""}
        book = self._s._book(note["book_id"])
        highlights = self._highlights(book)
        by_key = {h["key"]: h for h in highlights}
        chosen = [by_key[k] for k in idea["keys"] if k in by_key]
        stamp = str(vault.full(rel).stat().st_mtime)

        def work(token):
            vault_notes = self._notes(vault, fresh=True)
            skip = [note["title"], notes_module.hub_title(book["title"])]
            asked = [text, f"{note['title']} {idea.get('summary') or ''}"] + [h["text"] for h in chosen]
            messages = notes_ai.build_links(
                {"title": book["title"], "author": book["author"]}, {**idea, "title": note["title"]}, chosen, [],
                text, notes_ai.vault_index(vault_notes, skip=skip), self._prompt("links"),
                notes_ai.likely_notes(vault_notes, asked, per_text=20, limit=notes_ai.LINKS_FULL_NOTES, skip=skip))
            reply = self._ask("links", messages, token, LINKS_TIMEOUT)
            links, problem = notes_ai.parse_links(reply, [n["title"] for n in vault_notes], skip=skip)
            if problem:
                raise notes_module.VaultError(problem)
            self._store.update_note_written(note_id, related=links, related_at=time.time(), related_for=stamp)

        return {"started": self._start("links", note_id, note["book_id"], work)}

    @_route
    def written_open(self, note_id):
        _note, vault, rel, _text = self._written_parts(note_id)
        vault.open_note(rel)
        return {"ok": True}

    @_route
    def written_done(self, note_id):
        """Links back from every note the new one links to, as the user's rule says."""
        note, vault, rel, text = self._written_parts(note_id)
        typed = [t for t in notes_module.links_in(text) if t.lower() != note["title"].lower()]
        title = vault.full(rel).stem
        rels, names = [], []
        hub = notes_module.hub_title(self._s._book(note["book_id"])["title"]).lower()
        for name in typed:
            found = vault.find(name)
            if found:
                names.append(vault.full(found).stem)
                if name.lower() != hub:
                    rels.append(found)
        problems = self._link_back(vault, title, rels)
        linked = list(dict.fromkeys([*note["linked"], *names]))
        self._store.update_note_written(note_id, linked=linked, done_at=time.time(), title=title)
        self._cache["at"] = 0
        self._publish(note["book_id"], "written", target=note_id)
        return {"linked": names, "problems": problems}

    # The book's hub note

    def _hub_text(self, book, record, written, vault, titles):
        names = [w["title"] for w in written]
        hub = notes_module.hub_title(book["title"])
        existing = titles.get(hub.lower())
        if existing and vault:
            return notes_module.hub_add(existing["text"], names)
        return notes_module.hub_note(record["tag"], "", record["reflection"], names)

    @_route
    def hub(self, book_id):
        """Writes or updates the book's hub note, then opens it."""
        book = self._book(book_id)
        record = self._record(book)
        vault = self._vault()
        names = [w["title"] for w in self._store.notes_written(book_id)]
        hub = notes_module.hub_title(book["title"])
        rel = vault.find(hub)
        if rel:
            before = vault.read(rel)
            vault.change(rel, before, notes_module.hub_add(before, names))
        else:
            rel = vault.create(hub, notes_module.hub_note(record["tag"], "", record["reflection"], names))
        self._cache["at"] = 0
        vault.open_note(rel, new=True)
        self._publish(book_id, "hub")
        return {"ok": True, "title": hub}
