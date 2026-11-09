# Highlights to Flashcards: plan

A local web app, served by a small Python program on your computer, that turns a book's highlights into Anki flashcards. Highlights come from Readwise, the book and its Book Companion from Calibre, and cards go straight into your Anki collection through AnkiWeb sync. No e-reader needs to be plugged in.

It grew out of the pbAnki desktop companion. The card-making core (AI prompts, passages, connections, key ideas, clashes) is the same; everything that wrote to an e-reader is gone.

## 1. Ground rules

1. **The repo is public.** Nothing personal is ever committed: no names, emails, home folder paths, tokens, sync keys, collections, books or Companions. Test fixtures use public-domain books only. `scripts/privacy_check.py` runs before every commit and push (`git config core.hooksPath .githooks`), with your own words to block in `.git/info/private-words`.
2. **AnkiWeb is never overwritten.** The app keeps its own copy of your collection and syncs it like any Anki client. The first sync downloads. It never uploads a whole collection: when AnkiWeb asks for a full sync, the app downloads only if it has nothing unsynced, and otherwise stops and says so. It never changes an existing note type's fields or templates, since that forces a full sync.
3. **Secrets stay in the Keychain.** The Readwise token and the AnkiWeb sync key are stored with macOS `security`, passed on stdin, never in a file, a log, a URL or a command line. The AnkiWeb password is used once to get the sync key and then forgotten.
4. **Work is never lost.** Everything the user types or the AI writes goes to SQLite immediately.
5. **Local only.** The server binds `127.0.0.1` and checks `Host` and `Origin`, as before. Book text is untrusted input, so the AI runs with no tools.
6. **One dependency.** Anki's own Python package (`anki`, pinned) for the collection and sync. Everything else is the standard library. `uv` runs it, so `./run.sh` needs no install step.
7. **Plain English in the UI.** Comments are short and describe the code.

## 2. Sources

- **Readwise** (`readwise.py`). `GET /api/v2/export/` with `updatedAfter` set to the last pull, following `nextPageCursor`, keeps a local copy of every book and highlight. Deleted highlights are dropped. A 429 waits for `Retry-After`. A highlight's key is its Readwise id.
- **Calibre** (`calibre.py`). The library's `metadata.db`, opened read-only so it works while Calibre is open, gives each book's id, title, authors and ePub. The library is the one Calibre last used, or one picked in Settings. A Readwise book is matched by normalised title and author, then title alone; the user can pick another match, kept in settings. The ePub is the book's `.epub` format, and its Companion is `<epub stem>.companion.md` in the same folder, the same name an e-reader plugin would look for.
- **Missing context** is filled from the ePub: each highlight is found by its words (`epub.locate` without a position searches the whole book), which gives its passage, chapter and place in the book. Reading progress, used to keep key ideas and the Companion from running ahead, is taken from the furthest highlight.

## 3. The collection (`collection.py`)

`<home>/collection/collection.anki2`, opened with `anki.collection.Collection`.

- **Sync.** `sync_collection` with the stored key. `NO_CHANGES` and `NORMAL_SYNC` are done. `FULL_DOWNLOAD`, or `FULL_SYNC` with nothing unsynced here, downloads with `full_upload_or_download(upload=False)`. `FULL_UPLOAD`, or `FULL_SYNC` with local changes, stops with a plain sentence. Media isn't synced.
- **Cards.** New notes use the note type named in settings. The default is `pbAnki KOReader` (and `pbAnki KOReader Cloze`) when the collection has it, so pbAnki's Context screen keeps working; otherwise the app adds `Highlights to Flashcards` and `Highlights to Flashcards Cloze`, copies of Basic and Cloze with the fields `Quote, Book, Author, Chapter, Page, File, Position` after the first two. Decks are `<parent>::<book title>`, the parent from settings (`KOReader` when the pbAnki note type is there, else `Highlights`). Notes are tagged `highlights-to-flashcards`.
- **Reading** for clashes reads notes straight from the collection, in the shape `clashes.py` already uses.
- **Changing cards** (edit, suspend, unsuspend, delete) applies straight to the collection with the same refusals as pbAnki's queue (changed since read, formatting in a locked field, cloze numbers, empty, note type), then syncs.

## 4. Saving

The preview is unchanged: only opened highlights, connections and key ideas, blocked while a card can't be answered. Save adds the notes in one go, marks the highlights reviewed in SQLite, then syncs. If the sync fails the notes are already in the collection, and the next sync sends them. Reviewed status lives only here; the e-reader never learns of it.

## 5. Screens

Connect: Readwise, AnkiWeb and Calibre each with a status, and a Sync now button; it shows until all three are set. Start: make new cards or tidy up existing ones. Pick a book lists Readwise books with highlights, newest first, with a search box. The working screens (Suggest cards, Highlights, Connections, Key ideas, Save, Clashes) are as in pbAnki. Settings holds the three sources, the note type and parent deck, and, with a book open, which Calibre book it reads from. Write notes (`notes.py`, `notes_ai.py`, `notes_service.py`) has its own settings screen, then Think first, Ideas, Gather an idea, Writing, Not highlighted and Where it landed. It reads the Obsidian vault as Markdown, writes new notes as files, and changes other notes through Advanced URI while Obsidian runs.

## 6. Layout

```
run.py, run.sh, pyproject.toml
h2f/            server, service, store, runs, ai, cardgen, prompts, epub, companion, checks, clashes,
                changes, save, readwise, calibre, collection, library, keychain, paths
prompts/        Standard prompt files
web/            Preact + htm browser app, no build step
tests/          unittest, fakes for Readwise, Calibre and AnkiWeb, dev_server.py; fixtures are public-domain only
scripts/        privacy_check.py
```

Environment overrides, for tests: `H2F_HOME`, `H2F_CALIBRE_LIBRARY`, `H2F_READWISE_URL`, `H2F_SECRETS_FILE` (a JSON file in place of the Keychain), `H2F_CLAUDE_BIN`, `H2F_CODEX_BIN`.

## 7. Not in this version

- Telling the e-reader which highlights were handled.
- Media sync and cards with images.
- Windows and Linux (the Keychain and `open` are macOS only).
