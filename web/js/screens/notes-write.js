// Write notes: gathering one idea into a new note, and the panel beside Obsidian while you write it.

import { useState, useEffect, useRef } from "../../vendor/preact-htm.js";
import { go } from "../store.js";
import { html, Running, Pulse, plural, useTick } from "../ui.js";
import { useNotes, send, notesPath, NotesTop, ago } from "./notes.js";

const PAUSE_SECONDS = 20;
const MIN_WORDS = 12;

function noteText(tag, quotes, links) {
  const parts = [tag, ...quotes.map((q) => q.split("\n").map((line) => `> ${line}`).join("\n"))].filter(Boolean);
  return { head: parts.join("\n\n"), tail: links.length ? `See also: ${links.map((t) => `[[${t}]]`).join(", ")}` : "" };
}

function cleanTitle(text) {
  return text.normalize("NFC").replace(/[\\/:*?"<>|#^[\]\u0000-\u001f]/g, " ").replace(/\s+/g, " ").trim().replace(/^\.+|\.+$/g, "").trim();
}

function Highlights({ idea, shown, chosen, toggle, adding, setAdding, add }) {
  const [find, setFind] = useState("");
  const byKey = new Map(idea.highlights.map((h) => [h.key, h]));
  const rows = shown.map((key) => byKey.get(key)).filter(Boolean);
  const cards = rows.filter((h) => chosen.has(h.key)).flatMap((h) => h.cards);
  const words = find.toLowerCase().split(/\s+/).filter(Boolean);
  const others = idea.highlights.filter((h) => !shown.includes(h.key) && words.every((w) => h.text.toLowerCase().includes(w))).slice(0, 40);
  return html`<section>
    <div class="rowg" style="justify-content:space-between"><h2 class="nt-h2">From the book</h2><span class="nt-small">${idea.origin === "missed" ? "from the Book Companion" : plural(chosen.size, "highlight")}</span></div>
    ${idea.origin === "missed" && idea.quote && html`<div class="nt-hl"><label>${idea.chapter || "The book"}</label><p>${idea.quote}</p></div>`}
    ${rows.map(
      (h) => html`<div key=${h.key} class=${chosen.has(h.key) ? "nt-hl" : "nt-hl off"}>
        <label><input type="checkbox" checked=${chosen.has(h.key)} onChange=${() => toggle(h.key)} aria-label="Quote this in the note" />${h.chapter || "Highlight"} · ${chosen.has(h.key) ? "quoted" : "not quoted"}</label>
        <p>${h.text}</p>
        ${h.note && html`<span class="nt-hl-note">Your note: ${h.note}</span>`}
      </div>`,
    )}
    ${cards.length > 0 &&
    html`<div class="fl"><span class="lab">Your cards on this</span>${cards.slice(0, 6).map((c) => html`<div class="nt-card">${c.front}<br /><b>${c.back}</b></div>`)}${cards.length > 6 && html`<span class="nt-small">and ${plural(cards.length - 6, "more card")}</span>`}</div>`}
    ${adding
      ? html`<div class="fl">
          <input class="in" placeholder="Find a highlight in this book" value=${find} onInput=${(e) => setFind(e.target.value)} />
          <div class="nt-pick">${others.map((h) => html`<button key=${h.key} onClick=${() => add(h.key)}>${h.text}</button>`)}${!others.length && html`<span class="nt-small">No highlight matches that.</span>`}</div>
          <button class="lnk" onClick=${() => setAdding(false)}>Done adding</button>
        </div>`
      : html`<button class="lnk" style="align-self:flex-start" onClick=${() => setAdding(true)}>+ Add a highlight from elsewhere in the book</button>`}
  </section>`;
}

function Related({ idea, ticked, toggle, filter, setFilter, model }) {
  const disagrees = idea.related.filter((r) => r.kind === "disagrees").length;
  const shown = filter === "disagrees" ? idea.related.filter((r) => r.kind === "disagrees") : idea.related;
  const again = () => send("POST", `/api/notes/ideas/${idea.id}/related`);
  return html`<aside>
    <div class="fl">
      <h2 class="nt-h2">Where it sits in your slip-box</h2>
      ${idea.related_busy
        ? html`<${Running}>Reading all your notes for this idea<//>`
        : html`<span class="nt-small">${idea.related_at ? `Read your slip-box ${ago(idea.related_at)} · ${idea.related.length} found · ` : ""}<button class="lnk" onClick=${again}>Look again</button></span>`}
      <span class="nt-muted" style="font-size:13.5px">Ticked notes are linked both ways when you create the note: the new note links to them, and they link back to it.</span>
    </div>
    ${idea.related.length > 0 &&
    html`<div class="seg" style="align-self:flex-start">
      <button class=${filter === "all" ? "sg on" : "sg"} onClick=${() => setFilter("all")}>All ${idea.related.length}</button>
      <button class=${filter === "disagrees" ? "sg on" : "sg"} onClick=${() => setFilter("disagrees")}>Disagrees ${disagrees}</button>
    </div>`}
    <div>
      ${shown.map(
        (r) => html`<div key=${r.note} class="nt-rel">
          <label><input type="checkbox" checked=${ticked.has(r.note)} onChange=${() => toggle(r.note)} />${r.note}</label>
          <div class="nt-rel-m"><span class=${r.kind === "disagrees" ? "nt-kind disagrees" : "nt-kind"}>${r.kind}</span><span class="nt-small">${r.book || "your note"}</span></div>
          ${r.why && html`<p class="nt-why">${r.why}</p>`}
        </div>`,
      )}
      ${!idea.related_busy && idea.related_at && !idea.related.length && html`<div class="empty">Nothing in your slip-box is close to this yet. It may be a new branch.</div>`}
    </div>
  </aside>`;
}

function AddToExisting({ idea, close }) {
  const [find, setFind] = useState("");
  const words = find.toLowerCase().split(/\s+/).filter(Boolean);
  const matches = words.length ? idea.taken.filter((t) => words.every((w) => t.toLowerCase().includes(w))).slice(0, 30) : idea.related.map((r) => r.note);
  const pick = async (note) => {
    const answer = await send("POST", `/api/notes/ideas/${idea.id}/add-to`, { note });
    if (answer) go(notesPath("writing", idea.book_id, answer.note_id));
  };
  return html`<div class="fl">
    <span class="lab">Add the quoted highlights to an existing note</span>
    <input class="in" placeholder="Find a note by title" value=${find} onInput=${(e) => setFind(e.target.value)} autofocus />
    <div class="nt-pick">${matches.map((t) => html`<button key=${t} onClick=${() => pick(t)}>${t}</button>`)}${!matches.length && html`<span class="nt-small">No note matches that.</span>`}</div>
    <button class="lnk" style="align-self:flex-start" onClick=${close}>Cancel</button>
  </div>`;
}

export function Gather({ book: bookId, id }) {
  const { data: book } = useNotes(`/api/notes/books/${encodeURIComponent(bookId)}`);
  const { data: idea, error } = useNotes(`/api/notes/ideas/${encodeURIComponent(id)}`);
  const [title, setTitle] = useState(null);
  const [shown, setShown] = useState(null);
  const [chosen, setChosen] = useState(null);
  const [ticked, setTicked] = useState(null);
  const [adding, setAdding] = useState(false);
  const [existing, setExisting] = useState(false);
  const [filter, setFilter] = useState("all");
  const [busy, setBusy] = useState(false);
  useTick();

  useEffect(() => {
    if (!idea) return;
    const keys = idea.highlights.filter((h) => h.chosen).map((h) => h.key);
    if (shown === null) setShown(keys);
    if (chosen === null) setChosen(new Set(keys));
  }, [idea]);
  // New related notes start ticked, the ones the user unticked stay unticked
  const seen = useRef(new Set());
  useEffect(() => {
    if (!idea) return;
    const next = new Set(ticked || []);
    for (const r of idea.related) {
      if (!seen.current.has(r.note)) {
        seen.current.add(r.note);
        next.add(r.note);
      }
    }
    setTicked(next);
  }, [idea && idea.related.map((r) => r.note).join("\n")]);

  if (!idea || !book || !chosen || !ticked || !shown) {
    return html`<${NotesTop} book=${book} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Opening the idea<//>`}</div></div>`;
  }

  const saveKeys = (keys) => send("POST", `/api/notes/ideas/${idea.id}/keys`, { keys: [...keys] });
  const toggle = (key) => {
    const next = new Set(chosen);
    next.has(key) ? next.delete(key) : next.add(key);
    setChosen(next);
    saveKeys(next);
  };
  const add = (key) => {
    setShown([...shown, key]);
    const next = new Set(chosen).add(key);
    setChosen(next);
    saveKeys(next);
  };
  const toggleLink = (note) => {
    const next = new Set(ticked);
    next.has(note) ? next.delete(note) : next.add(note);
    setTicked(next);
  };

  const typed = title ?? "";
  const clean = cleanTitle(typed);
  const taken = clean && idea.taken.some((t) => t.toLowerCase() === clean.toLowerCase());
  const quotes = [
    ...(idea.origin === "missed" && idea.quote ? [idea.quote] : []),
    ...shown.filter((k) => chosen.has(k)).map((k) => idea.highlights.find((h) => h.key === k)).filter(Boolean).map((h) => h.text),
  ];
  const links = idea.related.filter((r) => ticked.has(r.note)).map((r) => r.note);
  const preview = noteText(idea.tag, quotes, links);
  const margin = idea.highlights.find((h) => chosen.has(h.key) && h.note);
  const questions = margin ? [`What were you thinking when you wrote “${margin.note}”?`, ...idea.questions.slice(1)] : idea.questions;
  const position = book.ideas.findIndex((i) => i.id === idea.id);
  const where = idea.origin === "missed" ? "Not highlighted" : position >= 0 ? `Idea ${position + 1} of ${book.ideas.length}` : "Idea";

  const create = async () => {
    setBusy(true);
    const answer = await send("POST", `/api/notes/ideas/${idea.id}/create`, { title: clean, link: links });
    setBusy(false);
    if (answer) go(notesPath("writing", idea.book_id, answer.note_id));
  };
  const dismiss = async () => {
    if (await send("POST", `/api/notes/ideas/${idea.id}/state`, { state: "dismissed" })) go(notesPath(idea.origin === "missed" ? "missed" : "ideas", idea.book_id));
  };

  return html`
    <${NotesTop} book=${book} tab=${idea.origin === "missed" ? "missed" : "ideas"} right=${html`<button class="lnk" onClick=${() => go(notesPath(idea.origin === "missed" ? "missed" : "ideas", idea.book_id))}>‹ All ideas</button>`} />
    <div class="nt-gather">
      <${Highlights} idea=${idea} shown=${shown} chosen=${chosen} toggle=${toggle} adding=${adding} setAdding=${setAdding} add=${add} />
      <section>
        <div class="nt-head">
          <span class="nt-eyebrow">${where} · ${idea.title}</span>
          <h1 class="h1">Why does this matter to you?</h1>
          <p class="lede">Not what the book says. What it means for what you already think. Give it a title, then write it yourself in Obsidian.</p>
        </div>
        ${idea.state === "written" && html`<div class="banner"><span>You already wrote a note from this idea.</span>${idea.note_id && html`<button class="btn" onClick=${() => go(notesPath("writing", idea.book_id, idea.note_id))}>Open it</button>`}</div>`}
        <div class="fl">
          <label class="lab" for="title">Title, as a file name</label>
          <input id="title" class="in nt-title" value=${typed} placeholder="a short handle or a plain claim" onInput=${(e) => setTitle(e.target.value)} />
          <div class="nt-sugg">
            <span class="nt-small">Ideas for a title:</span>
            ${idea.titles_busy && html`<span class="nt-small rowg"><${Pulse} />thinking</span>`}
            ${idea.titles.map((t) => html`<button key=${t} onClick=${() => setTitle(t)}>${t}</button>`)}
            ${!idea.titles_busy && html`<button class="lnk" style="border:0;background:none" onClick=${() => send("POST", `/api/notes/ideas/${idea.id}/titles`)}>more</button>`}
          </div>
          ${clean &&
          html`<span class="nt-small">${taken ? html`There's already a note called “${clean}”. <button class="lnk" onClick=${() => setExisting(true)}>Add to it instead</button>` : `No note is called “${clean}” yet.`}</span>`}
        </div>
        <div class="fl">
          <span class="lab">The new file starts as</span>
          <pre class="nt-pre">${preview.head}${"\n\n"}<i>▍ your words go here</i>${preview.tail ? `\n\n${preview.tail}` : ""}</pre>
          <span class="nt-small">Tick highlights on the left to quote them, and notes on the right to link them. Move any link into your sentences as you write.</span>
        </div>
        <div class="fl">
          <span class="lab">Before you write, ask yourself</span>
          <ul class="nt-ask">${questions.map((q) => html`<li>${q}</li>`)}</ul>
        </div>
        ${existing
          ? html`<${AddToExisting} idea=${idea} close=${() => setExisting(false)} />`
          : html`<div class="rowg">
              <button class="btn pri nt-big" disabled=${!clean || taken || busy || !idea.vault_ok} onClick=${create}>${busy ? "Creating..." : clean ? `Create “${clean}” and open in Obsidian` : "Pick a title first"}</button>
              <button class="btn" onClick=${() => setExisting(true)}>Add to an existing note…</button>
              <button class="btn" onClick=${dismiss}>Dismiss</button>
            </div>`}
      </section>
      <${Related} idea=${idea} ticked=${ticked} toggle=${toggleLink} filter=${filter} setFilter=${setFilter} />
    </div>
  `;
}

// Beside Obsidian

export function Writing({ book: bookId, id }) {
  const { data: book } = useNotes(`/api/notes/books/${encodeURIComponent(bookId)}`);
  const { data: note, error } = useNotes(`/api/notes/written/${encodeURIComponent(id)}`, { every: 2000 });
  const [result, setResult] = useState(null);
  const asked = useRef(null);
  useTick();

  // Looks again once the note has sat unchanged for a while since the last look
  useEffect(() => {
    if (!note || note.related_busy || note.done) return;
    const stamp = String(note.saved_at);
    const quiet = Date.now() / 1000 - note.saved_at;
    if (note.related_for !== stamp && asked.current !== stamp && quiet > PAUSE_SECONDS && note.words >= MIN_WORDS) {
      asked.current = stamp;
      send("POST", `/api/notes/written/${note.id}/related`);
    }
  });

  if (!note || !book) {
    return html`<${NotesTop} book=${book} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Opening the note<//>`}</div></div>`;
  }
  const done = async () => {
    const answer = await send("POST", `/api/notes/written/${note.id}/done`);
    if (answer) {
      setResult(answer);
      if (!answer.problems.length) go(notesPath("landed", note.book_id));
    }
  };
  const again = () => send("POST", `/api/notes/written/${note.id}/related`);
  const open = () => send("POST", `/api/notes/written/${note.id}/open`);

  return html`
    <${NotesTop} book=${book} right=${html`<button class="lnk" onClick=${() => go(notesPath("ideas", note.book_id))}>‹ All ideas</button>`} />
    <div class="nt-writing"><div class="nt-panel">
      <div class="rowg" style="justify-content:space-between">
        <h1 class="h1" style="font-size:22px">Writing “${note.title}”</h1>
        <span class="chip"><span class="dot"></span>Saved by Obsidian ${ago(note.saved_at)}</span>
      </div>
      <p class="nt-muted">Keep this beside Obsidian. It reads the note each time Obsidian saves it. ${note.added ? "The highlights were added to the end of this note." : ""} <button class="lnk" onClick=${open}>Open in Obsidian</button></p>

      <div class="fl">
        <span class="nt-h2" style="font-size:13px">Linked so far · ${note.linked.length}</span>
        <div class="nt-chips">${note.linked.map((l) => html`<span key=${l.title} class="nt-chip">✓ ${l.title}</span>`)}</div>
        ${note.linked.length > 0 && html`<span class="nt-small">${note.other_books ? `Spans ${plural(note.other_books, "book")}.` : "All your own notes so far."}</span>`}
        ${note.missing.length > 0 && html`<span class="nt-small">Not in your slip-box yet: ${note.missing.map((m) => `[[${m}]]`).join(", ")}. These get no link back.</span>`}
      </div>

      <div class="nt-sec" style="gap:2px">
        <div class="rowg" style="justify-content:space-between;align-items:baseline">
          <span class="nt-h2" style="font-size:13px">What your words touch now</span>
          ${!note.related_busy && html`<button class="lnk" onClick=${again}>Look again</button>`}
        </div>
        <span class="nt-small" style="margin-bottom:6px">${note.related_busy
          ? html`<span class="rowg"><${Pulse} />Reading your slip-box with what you've written</span>`
          : note.related_for ? `Looked again when you paused, ${ago(Number(note.related_for))}` : "From before you started writing. It looks again when you pause."}</span>
        ${note.related.map(
          (r) => html`<div key=${r.note} class="nt-rel">
            <span style="font:500 13.5px var(--mono)">${r.note}</span>
            <div class="rowg" style="align-items:baseline">
              ${r.new && html`<span class="nt-new">NEW</span>`}
              <span class=${r.kind === "disagrees" ? "nt-kind disagrees" : "nt-kind"}>${r.kind}</span>
              <span class="nt-small">${r.book || "your note"}</span>
            </div>
            ${r.why && html`<p class="nt-muted" style="font-size:13.5px">${r.why}</p>`}
          </div>`,
        )}
      </div>

      <details class="nt-sec" style="gap:8px">
        <summary><span class="nt-h2" style="font-size:13px">The highlights · ${note.highlights.length}</span></summary>
        ${note.highlights.map((h) => html`<div class="nt-hl" style="margin-top:8px"><p>${h.text}</p>${h.note && html`<span class="nt-hl-note">Your note: ${h.note}</span>`}</div>`)}
      </details>
      <details class="nt-sec" style="border-top-width:1px">
        <summary><span class="nt-h2" style="font-size:13px">Ask yourself</span></summary>
        <ul class="nt-ask" style="margin-top:8px">${note.questions.map((q) => html`<li>${q}</li>`)}</ul>
      </details>

      ${result && result.problems.length > 0 &&
      html`<div class="banner"><span>Some links back didn't go in: ${result.problems.join(" ")}</span><button class="btn" onClick=${() => go(notesPath("landed", note.book_id))}>Carry on</button></div>`}
      <div class="nt-foot">
        <button class="btn pri nt-big" onClick=${done}>${note.done ? "Done again" : "Done writing"}</button>
        <span class="nt-small">Notes you link while writing get a link back when you press Done.</span>
      </div>
    </div></div>
  `;
}
