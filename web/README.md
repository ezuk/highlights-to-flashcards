# Highlights to Flashcards browser app

Preact + htm from `vendor/preact-htm.js`, no build step. Import `html`, hooks and `render` from there. `companion.css` holds every look. Reuse its classes and don't add new ones.

Try it with `.venv/bin/python tests/dev_server.py --port 8791`, which uses fake Readwise, Calibre, AnkiWeb and AI in a throwaway folder. The fake Readwise token is `test-token`, and any email logs in to the fake AnkiWeb with the password `test-password`. `--connected` starts with both connected.

**Store (`js/store.js`).** One object, `store`: `state` (GET /api/state), `books`, `book` (GET /api/books/<id>), `route`, `ui`, `error`. `const s = useStore()` re-renders the component on any change. Local fields go in `ui` with `setUi({...})`; keep them there when they must outlive a screen.

- `mutate(method, path, body)` sends a change and refetches the open book. It answers with the response, or shows the error and answers null. Pass `{ quiet: true }` to get a throw instead.
- `saveSettings({...})` is PATCH /api/settings. `syncNow(parts)`, `saveReadwiseToken`, `ankiwebLogin`, `ankiwebLogout` and `chooseCalibreLibrary` change the sources and throw on a refusal. `showError(sentence)` and `clearError()` drive the error bar. `openBook(id, screen)` picks a book.
- `refreshBook()`, `loadState()`, `loadBooks()` refetch by hand. A book fetched across a write is thrown away and fetched again, so never merge responses into `store.book` by hand.

**Routing.** The hash: `#/connect`, `#/start`, `#/books`, `#/clashes`, `#/book/<id>/<screen>[/<sub>]`, with screens `suggest`, `highlights`, `connections`, `ideas`, `save`, `saved`. `route` is `{name, bookId, sub, query}`. Use `sub` for the selected highlight, connection or idea (`find` for Find connections), so a reload keeps it. Move with `go(here("highlights", hid))` or `go(bookPath(id, "save"))`. `app.js` only shows a book screen once `store.book` is that book, so screens can read it without checks. Keep a screen's own export names, as `app.js` imports them.

**Drawers.** `openDrawer("prompt", { name, file })` and `closeDrawer()`. Parameters arrive in `route.query`, and any drawer can open over any screen. Each open is one step of browser history, so Back closes it.

**Events.** `store.js` holds the event stream and already refetches what changed: `sources` and `settings` and `prompts` update `state`, and `book:<id>` updates `book`. For anything more, such as an error from a finished `suggest` or refetching an open prompt, use `useEvent((event) => ...)`, with `useCallback` or a stable function. Outside a component, `onEvent(listener)` does the same and answers a function that stops listening. All tabs share one stream through `js/events-worker.js`. Events are listed under "Events" in the API notes.

**Edits.** Use `Field` from `ui.js` for anything saved as it's typed, with a `key` per record. A record's field gives the route and the field name:

```js
html`<${Field} key=${`${card.id}-front`} class="ta f" rows="1" value=${card.front} placeholder="Question"
  path=${`/api/cards/${card.id}`} field="front" />`
```

It saves after 300 ms of quiet and on blur, flushes when unmounted or the page goes away, and doesn't let a refetch overwrite what's being typed. Text the server hasn't taken stays on screen, is kept in the browser and is sent again until it goes through, with a bar saying so. Anything that isn't a record's field gives `onSave(text)` instead, returning a promise that rejects when the save failed: the text stays, the error shows and the next blur tries again. For something else that debounces, use `debouncedSave(save, ms)` from the store.

Remove a record with `removeRecord(path)`, which sends its edits first and forgets what's left. Before anything that reads what was typed, such as a save, `await editsSent(prefix)`.

**Shared pieces (`js/ui.js`).** `Top` (brand, `middle`, sync status chip, Settings, `children` at the right end, error bar), `WorkTop` (the bar of the three working tabs with Save), `BookButton`, `ReaderChip` (the sync status), `ErrorBar`, `Pulse`, `Running`, `CheckRow`, `Opt`, `Select`, `Stepper`, `PromptLine`, `EinkBar`, `Drawer`, `Field`, `Waiting`, `useTick`, `useReloadAgo`, `plural`, `isTyping` (for shortcuts, which ignore keys while typing). A screen without `Top` renders `ErrorBar` itself. Bars go in a `.bars` wrapper that always renders: when a sibling of a screen appears or goes, Preact can move the screen in the page, which drops the cursor.

Unknown body fields get a 400 from the server, so send only what a route accepts.
