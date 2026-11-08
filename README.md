# Highlights to Flashcards

Turn the passages you highlight in books into Anki flashcards, with an AI doing the first draft and you deciding what stays.

It runs on your Mac as a small local web app. Your highlights come from [Readwise](https://readwise.io), the book itself comes from your [Calibre](https://calibre-ebook.com) library, and the cards go into your [Anki](https://apps.ankiweb.net) collection through AnkiWeb sync, so they show up on every device you review on.

## What it does

1. **Syncs your highlights from Readwise**, books only, and keeps a copy on your computer.
2. **Finds each book in Calibre** and reads the ePub, so every highlight comes with the passage around it and the chapter it's in. You can pick a different Calibre book if the match is wrong.
3. **Suggests cards** for each highlight with Claude Code or Codex: questions and answers, cloze deletions or definitions, written in your style from the cards you've kept before. It also looks for connections across chapters, and for key ideas you didn't highlight when the book has a Book Companion (long notes on the whole book).
4. **You review**: open a highlight, keep, edit or remove its cards, or write your own.
5. **Checks for clashes**: cards whose questions read alike but whose answers differ, so a review can't tell which answer it wants. It checks new cards against each other and your whole collection, and can tidy up clashes already in your collection.
6. **Saves and syncs**: new cards go into your collection, each book in its own deck, and AnkiWeb gets them at once.

## What you need

- A Mac. The app keeps your Readwise token and AnkiWeb sync key in the macOS Keychain.
- [uv](https://docs.astral.sh/uv/), which fetches the right Python and the one dependency, Anki's own Python package.
- A Readwise account with book highlights. Any reader that syncs highlights to Readwise works.
- An AnkiWeb account.
- A Calibre library with your books as ePubs. Without one, the AI sees only the highlight itself.
- [Claude Code](https://claude.com/claude-code) or [Codex](https://github.com/openai/codex) installed and signed in. The app runs them on your computer to write cards.

## Start it

```bash
git clone https://github.com/ezuk/highlights-to-flashcards.git
cd highlights-to-flashcards
./run.sh
```

It opens in your browser. Paste a Readwise access token (from readwise.io/access_token), log in to AnkiWeb, and check it found your Calibre library. Then pick a book.

## How it treats your data

- **Everything stays on your computer.** The server listens only on your own machine. Your highlights, drafts and a backup of every save live in `~/Library/Application Support/Highlights to Flashcards`.
- **AnkiWeb is never overwritten.** The app keeps its own copy of your collection and syncs it like any Anki client. The first sync downloads your collection. It never uploads a whole collection: if AnkiWeb ever asks for a full sync and the app has cards AnkiWeb hasn't seen, it stops and tells you instead of choosing for you.
- **Your AnkiWeb password is used once**, to get a sync key, and isn't kept. The sync key and Readwise token go in the Keychain.
- **The AI gets only what it needs**: the highlights, the nearby passage of the book, and your earlier cards from that book. It runs with no tools.
- **Cards that change** (edits, suspensions, deletions from the clash screen) go into your collection right away and to AnkiWeb with the next sync, a few seconds later. Each one can be undone.

## Cards it makes

New notes use a note type called `Highlights to Flashcards` (and `Highlights to Flashcards Cloze`), copies of Anki's Basic and Cloze with these fields after the question and answer: Quote, Book, Author, Chapter, Page, File, Position. They go in `Highlights::<book title>` and are tagged `highlights-to-flashcards`. You can change the note type and parent deck in Settings.

If your collection already has the `pbAnki KOReader` note type from the pbAnki KOReader plugin, the app uses it and its `KOReader` deck instead, so cards keep their context on the e-reader.

## Prompts

The instructions the AI follows are plain Markdown files in `prompts/Standard`. To write your own, copy that folder next to it, rename the copy, and pick it in the app. The app puts `Standard` back each time it starts.

## Book Companions

A Book Companion is a long Markdown file of notes on a whole book: each chapter's key points, its vocabulary, and what later research says. With one, the AI can find key ideas you didn't highlight. The app looks for `<ePub name>.companion.md` beside the ePub in Calibre. It can also create one by running a Claude Code `book-companion` skill, if you have one.

## Development

```bash
.venv/bin/python -m unittest discover -s tests
.venv/bin/python tests/dev_server.py --port 8791
```

The first runs the tests. The second runs the app against fake Readwise, Calibre, AnkiWeb and AI in a throwaway folder: the Readwise token is `test-token`, and any email logs in with the password `test-password`. Test books are public domain.

The repo has a privacy guard. Turn it on once with `git config core.hooksPath .githooks`, and put words that identify you, one per line, in `.git/info/private-words`. It then blocks any commit or push that has those words, a home folder path, an email address, a token, or a collection, library or book file.

`PLAN.md` describes how it works inside, and `web/README.md` the browser app.

## License

[AGPL-3.0-or-later](LICENSE), the same as Anki's own code, which this app uses.
