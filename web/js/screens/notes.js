// Write notes: settings, picking a book, thinking first, the ideas, what wasn't highlighted and where it landed.

import { useState, useEffect, useCallback, useRef } from "../../vendor/preact-htm.js";
import { useStore, useEvent, go, showError, clearError, loadBooks } from "../store.js";
import { api } from "../api.js";
import { html, ErrorBar, Running, Pulse, Field, plural, useTick } from "../ui.js";
import { Gather, Writing } from "./notes-write.js";

// Routes

export const notesPath = (screen, book, item) =>
  `#/notes/${screen}` + (book ? `/${encodeURIComponent(book)}` : "") + (item ? `/${encodeURIComponent(item)}` : "");

// Fetches `path` and again on each notes event. Answers {data, error, reload}.
export function useNotes(path, { every = 0 } = {}) {
  const [answer, setAnswer] = useState({ path: null, data: null, error: null });
  const latest = useRef(path);
  latest.current = path;
  const reload = useCallback(async () => {
    if (!path) return;
    try {
      const data = await api.get(path);
      if (latest.current === path) setAnswer({ path, data, error: null });
    } catch (error) {
      if (latest.current === path) setAnswer((prev) => ({ ...prev, path, error: error.message }));
    }
  }, [path]);
  useEffect(() => {
    reload();
  }, [reload]);
  useEffect(() => {
    if (!every) return undefined;
    const timer = setInterval(reload, every);
    return () => clearInterval(timer);
  }, [reload, every]);
  useEvent(
    useCallback(
      (event) => {
        if (event.topic && (event.topic.startsWith("notes") || event.topic === "prompts")) reload();
      },
      [reload],
    ),
  );
  return answer.path === path ? { ...answer, reload } : { data: null, error: null, reload };
}

// Sends a change. Answers the response, or null after showing the error.
export async function send(method, path, body) {
  try {
    const verb = { POST: api.post, PATCH: api.patch }[method];
    const answer = await verb(path, body);
    clearError();
    return answer;
  } catch (error) {
    showError(error);
    return null;
  }
}

export const ago = (stamp) => {
  if (!stamp) return "";
  const s = Math.max(0, Math.round(Date.now() / 1000 - stamp));
  if (s < 60) return `${s} s ago`;
  if (s < 3600) return `${Math.round(s / 60)} min ago`;
  if (s < 86400) return `${Math.round(s / 3600)} h ago`;
  return `${Math.round(s / 86400)} days ago`;
};

// The bar across the top of the notes screens. `tab` is the screen showing.
export function NotesTop({ book, tab, right }) {
  const tabs = book && [
    ["ideas", `Ideas ${book.ideas.length}`],
    ["missed", `Not highlighted ${book.missed.length}`],
    ["landed", "Where it landed"],
  ];
  return html`
    <div class="top">
      <button class="brand" onClick=${() => go("#/start")}>Highlights to Flashcards</button>
      ${book
        ? html`<button class="bookbtn" onClick=${() => go(notesPath("books"))} style="max-width:300px">
            <span class="bookt">${book.title}</span>
            <span class="booka">${book.author && html`<span class="bookau">${book.author}</span>`}<span class="bookc">${book.author ? " · " : ""}change book</span></span>
          </button>`
        : html`<span class="crumbt">Write notes</span>`}
      ${tabs &&
      html`<div class="seg">
        ${tabs.map(([id, label]) => html`<button key=${id} class=${tab === id ? "sg on" : "sg"} onClick=${() => go(notesPath(id, book.id))}>${label}</button>`)}
      </div>`}
      <div class="topr">${right}<button class="lnk" onClick=${() => go(notesPath("setup"))}>Settings</button></div>
    </div>
    <${ErrorBar} />
  `;
}

// Markdown, for the prompt preview: headings, lists, paragraphs, bold, italics and code.
function inline(text) {
  const parts = [];
  const re = /(`[^`]+`|\*\*[^*]+\*\*|\*[^*]+\*)/g;
  let last = 0;
  let match;
  while ((match = re.exec(text))) {
    if (match.index > last) parts.push(text.slice(last, match.index));
    const token = match[0];
    if (token.startsWith("`")) parts.push(html`<code>${token.slice(1, -1)}</code>`);
    else if (token.startsWith("**")) parts.push(html`<strong>${token.slice(2, -2)}</strong>`);
    else parts.push(html`<em>${token.slice(1, -1)}</em>`);
    last = match.index + token.length;
  }
  if (last < text.length) parts.push(text.slice(last));
  return parts;
}

export function Markdown({ text }) {
  const blocks = [];
  let list = null;
  let para = [];
  const flushPara = () => {
    if (para.length) blocks.push(html`<p>${inline(para.join(" "))}</p>`);
    para = [];
  };
  const flushList = () => {
    if (list) blocks.push(list.ordered ? html`<ol>${list.items}</ol>` : html`<ul>${list.items}</ul>`);
    list = null;
  };
  for (const raw of (text || "").split("\n")) {
    const line = raw.trimEnd();
    const heading = line.match(/^(#{1,6})\s+(.*)$/);
    const bullet = line.match(/^\s*[-*]\s+(.*)$/);
    const numbered = line.match(/^\s*\d+[.)]\s+(.*)$/);
    if (heading) {
      flushPara();
      flushList();
      const Tag = `h${Math.min(heading[1].length + 1, 4)}`;
      blocks.push(html`<${Tag}>${inline(heading[2])}<//>`);
    } else if (bullet || numbered) {
      flushPara();
      const ordered = !!numbered;
      if (!list || list.ordered !== ordered) {
        flushList();
        list = { ordered, items: [] };
      }
      list.items.push(html`<li>${inline((bullet || numbered)[1])}</li>`);
    } else if (!line.trim()) {
      flushPara();
      flushList();
    } else {
      flushList();
      para.push(line.trim());
    }
  }
  flushPara();
  flushList();
  return html`<article class="nt-md" aria-label="Rendered prompt">${blocks}</article>`;
}

// Settings

function SlipBox({ setup }) {
  const vault = setup.vault;
  const [typed, setTyped] = useState(vault.path);
  const choose = () => send("POST", "/api/notes/choose-vault");
  const save = () => send("PATCH", "/api/notes/settings", { vault: typed });
  const summary = vault.ok
    ? `${vault.name} · ${plural(vault.notes, "note")}${vault.advanced_uri ? " · ✓ Advanced URI" : ""}${vault.git ? " · ✓ git backup" : ""}`
    : "Not chosen yet";
  return html`<details class="nt-sec" open=${!vault.ok}>
    <summary>
      <h2 class="nt-h2">Slip-box</h2>
      <span class="nt-small">${summary}</span>
      <span class="lnk">Change</span>
    </summary>
    <div class="nt-drop">
      <div class="fl">
        <span class="lab">Obsidian vault</span>
        <input class="in" value=${typed} placeholder="~/path/to/your/vault" onInput=${(e) => setTyped(e.target.value)} onKeyDown=${(e) => e.key === "Enter" && save()} />
        <div class="rowg">
          <button class="btn" onClick=${choose}>Choose…</button>
          ${typed !== vault.path && html`<button class="btn" onClick=${save}>Use this folder</button>`}
          ${vault.ok && html`<span class="nt-small">${plural(vault.notes, "note")} · read just now</span>`}
        </div>
      </div>
      ${vault.ok &&
      html`<div class="nt-checks">
        <b>${vault.advanced_uri ? "✓" : "!"}</b><span>${vault.advanced_uri
          ? "Advanced URI plugin is on, so links in other notes are changed through Obsidian."
          : "Advanced URI plugin isn't on. Links in other notes are changed on disk, so close those notes in Obsidian first."}</span>
        <b>${vault.git ? "✓" : "!"}</b><span>${vault.git
          ? "The vault is backed up with git, so every change can be undone."
          : "The vault isn't in git, so changes to other notes can't be undone from here."}</span>
      </div>`}
      ${vault.problem && html`<div class="nt-small">${vault.problem}</div>`}
    </div>
  </details>`;
}

const PROVIDER_DEFAULTS = {
  claude: { ideas: "claude-opus-5-5", links: "claude-opus-5-5", titles: "claude-sonnet-5-5" },
};

function AiSection({ setup }) {
  const current = (setup.models.find((m) => m.id === setup.jobs[0].model) || {}).a || "claude";
  const providers = setup.assistants.filter((a) => setup.models.some((m) => m.a === a.id));
  const pick = (id) => {
    const choices = setup.models.filter((m) => m.a === id);
    const defaults = PROVIDER_DEFAULTS[id] || {};
    const models = {};
    for (const job of setup.jobs) {
      models[job.id] = choices.some((m) => m.id === defaults[job.id]) ? defaults[job.id] : choices[choices.length - 1].id;
    }
    send("PATCH", "/api/notes/settings", { models });
  };
  const choices = setup.models.filter((m) => m.a === current);
  return html`<section class="nt-sec">
    <h2 class="nt-h2">AI</h2>
    <div class="fl">
      <span class="lab">Runs with</span>
      <div class="seg" role="radiogroup" aria-label="AI provider" style="align-self:flex-start">
        ${providers.map((a) => html`<button key=${a.id} role="radio" aria-checked=${a.id === current} class=${a.id === current ? "sg on" : "sg"} onClick=${() => a.id !== current && pick(a.id)}>${a.name}</button>`)}
      </div>
      <span class="nt-small rowg"><span class="dot"></span>${providers.length ? "Found · runs on this Mac with no tools" : "Neither Claude Code nor Codex is set up. Check Settings."}</span>
    </div>
    ${setup.jobs.map(
      (job) => html`<div key=${job.id} class="nt-job">
        <label for=${`job-${job.id}`}>${job.label}</label>
        <select id=${`job-${job.id}`} class="sel" value=${job.model} onChange=${(e) => send("PATCH", "/api/notes/settings", { models: { [job.id]: e.target.value } })}>
          ${choices.map((m) => html`<option key=${m.id} value=${m.id} selected=${m.id === job.model}>${m.label}</option>`)}
          ${!choices.some((m) => m.id === job.model) && html`<option value=${job.model} selected>${job.model}</option>`}
        </select>
        <span class="nt-small">${job.note}</span>
      </div>`,
    )}
  </section>`;
}

function PromptsSection({ setup, reload }) {
  const [tab, setTab] = useState("notes-links");
  const [at, setAt] = useState(Date.now());
  useEffect(() => setAt(Date.now()), [setup]);
  useTick();
  const file = setup.files.find((f) => f.kind === tab) || setup.files[0];
  const reloadAgo = setup.reloadAgo + Math.floor((Date.now() - at) / 1000);
  const launch = async (action) => {
    if (await send("POST", `/api/prompts/${action}`, { name: setup.prompt, file: file.file })) reload();
  };
  const reloadNow = async () => {
    await send("POST", "/api/prompts/reload");
    reload();
  };
  return html`<section class="nt-main nt-sec">
    <div class="rowg" style="justify-content:space-between">
      <h2 class="nt-h2">Prompts</h2>
      <div class="rowg">
        <label class="lab" for="pset">Prompt</label>
        <select id="pset" class="sel" value=${setup.prompt} onChange=${(e) => send("PATCH", "/api/notes/settings", { prompt: e.target.value })}>
          ${setup.prompt_names.map((n) => html`<option key=${n} value=${n} selected=${n === setup.prompt}>${n}</option>`)}
        </select>
      </div>
    </div>
    <div class="seg nt-tabs" role="tablist" aria-label="Prompts by job">
      ${setup.files.map((f) => html`<button key=${f.kind} role="tab" aria-selected=${f === file} class=${f === file ? "sg on" : "sg"} onClick=${() => setTab(f.kind)}>${f.label}</button>`)}
    </div>
    ${file &&
    html`<div class="rowg">
        <span class="nt-small" style="overflow-wrap:anywhere">${file.display}${file.status === "uses Standard" ? " · uses Standard" : ""}</span>
        <span style="margin-left:auto"></span>
        <button class="btn pri" onClick=${() => launch("open")}>Open in editor</button>
        <button class="btn" onClick=${reloadNow}>Reload</button>
        <button class="btn" onClick=${() => launch("reveal")}>Show in Finder</button>
      </div>
      <span class="nt-small rowg"><${Pulse} />Watching for changes · reloaded ${reloadAgo} s ago${setup.prompt === "Standard" ? " · your edits to Standard are kept" : ""}</span>
      <${Markdown} text=${file.text} />`}
  </section>`;
}

function Setup() {
  const { data: setup, error, reload } = useNotes("/api/notes/setup");
  if (!setup) return html`<${NotesTop} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Reading your settings<//>`}</div></div>`;
  return html`
    <${NotesTop} />
    <div class="nt-page"><div class="nt-wrap">
      <div class="nt-head">
        <h1 class="h1">Before you start</h1>
        <p class="lede" style="max-width:64ch">Check where your notes go, which AI does the reading, and what it's told. These stay as you leave them for the next book.</p>
      </div>
      <div class="nt-cols">
        <div class="nt-side">
          <${SlipBox} key=${setup.vault.path} setup=${setup} />
          <${AiSection} setup=${setup} />
        </div>
        <${PromptsSection} setup=${setup} reload=${reload} />
      </div>
      <div class="nt-foot">
        <button class="btn pri nt-big" disabled=${!setup.vault.ok} onClick=${() => go(notesPath("books"))}>Pick a book</button>
        <span class="nt-small">${setup.vault.ok ? "Your notes, highlights and the book stay on this Mac. Only the AI you picked reads them." : "Choose your slip-box first."}</span>
      </div>
    </div></div>
  `;
}

// Pick a book

function Books() {
  const { books } = useStore();
  const [filter, setFilter] = useState("");
  useEffect(() => {
    loadBooks().catch(() => {});
  }, []);
  const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
  const shown = (books || []).filter((b) => words.every((w) => `${b.title} ${b.author}`.toLowerCase().includes(w)));
  return html`
    <${NotesTop} />
    <div class="nt-page"><div class="nt-wrap" style="max-width:900px">
      <div class="nt-head"><h1 class="h1">Pick a book</h1><p class="lede">Most recently highlighted first.</p></div>
      ${!books
        ? html`<${Running}>Reading your highlights<//>`
        : html`<input class="in" placeholder="Find a book" value=${filter} onInput=${(e) => setFilter(e.target.value)} />
          <div class="nt-books">
            ${shown.map(
              (b) => html`<button key=${b.id} class="nt-book" onClick=${() => go(notesPath("think", b.id))} disabled=${b.none}>
                <span><span class="bkt" style="display:block">${b.title}</span><span class="bka">${b.author}</span></span>
                <span class="bkm">${b.hl}</span>
                <span class="bkgo">Open</span>
              </button>`,
            )}
            ${!shown.length && html`<div class="empty">No book matches that.</div>`}
          </div>`}
    </div></div>
  `;
}

// Think about the book first

function Progress({ book, retry }) {
  const gathering = book.status === "gathering";
  const done = book.status === "ready" || book.ideas.length > 0;
  const mark = (on, busy) => (on ? "✓" : busy ? "●" : "○");
  return html`<div class="nt-progress">
    <b>${gathering ? html`<${Pulse} />Meanwhile, the AI is reading` : done ? "The AI has read it" : "The AI hasn't started"}</b>
    <span>✓ ${plural(book.highlight_count, "highlight")}, ${plural(book.card_count, "card")}${book.companion ? " · ✓ Book Companion" : ""} · ✓ ${plural(book.vault.notes, "note")} in your slip-box · ${mark(done && !gathering, gathering)} ${done && !gathering ? `${plural(book.ideas.length, "idea")} gathered` : "gathering ideas"}${book.companion ? ` · ${mark(done && !gathering, gathering)} ideas you didn't highlight` : ""}</span>
    ${book.status === "failed" && html`<span>${book.error} <button class="lnk" onClick=${retry}>Try again</button></span>`}
    ${done && !gathering && html`<span class="ready">Ready when you are</span>`}
  </div>`;
}

const gather = (id) => send("POST", `/api/notes/books/${encodeURIComponent(id)}/gather`);

function Think({ id }) {
  const { data: book, error } = useNotes(`/api/notes/books/${encodeURIComponent(id)}`);
  const started = useRef(false);
  useEffect(() => {
    if (book && !started.current && !book.status && !book.ideas.length && book.vault.ok) {
      started.current = true;
      gather(id);
    }
  }, [book]);
  if (!book) return html`<${NotesTop} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Opening the book<//>`}</div></div>`;
  const path = `/api/notes/books/${encodeURIComponent(id)}`;
  const saveTag = async (text) => {
    if (!(await send("PATCH", path, { tag: text }))) throw new Error("not saved");
  };
  return html`
    <${NotesTop} book=${book} />
    <div class="nt-page"><div class="nt-wrap nt-think">
      <div class="nt-head">
        <span class="nt-eyebrow">Before your notes</span>
        <h1 class="h1">Take a moment and just think about the book.</h1>
        <p class="lede" style="max-width:62ch">What stayed with you? Write it from memory, in your own words, before you see a single highlight. What you remember now is what the book really gave you. It goes in the book's own note, and the AI never sees it.</p>
      </div>
      <div class="fl">
        <${Field} key=${`reflect-${id}`} class="nt-recall" value=${book.reflection} placeholder="- " onSave=${(text) => api.patch(path, { reflection: text })} />
        <span class="nt-small">Saved as you type. It opens the book's own note, “${book.hub.title}”, the way your other book notes begin.</span>
      </div>
      <div class="fl" style="max-width:420px">
        <label class="lab" for="tag">Tag for this book's notes</label>
        <${Field} key=${`tag-${id}`} tag="input" id="tag" class="in" commit value=${book.tag} onSave=${saveTag} />
        <span class="nt-small">${book.vault.tagged ? `${plural(book.vault.tagged, "note")} already use it.` : "Suggested from the title, without “The”. No note uses it yet."} Every note you write from this book gets it.</span>
      </div>
      <div class="rowg">
        <button class="btn pri nt-big" onClick=${() => go(notesPath("ideas", id))}>Now go over my highlights</button>
        <button class="lnk" onClick=${() => go(notesPath("ideas", id))}>Skip this</button>
      </div>
    </div></div>
    <${Progress} book=${book} retry=${() => gather(id)} />
  `;
}

// The ideas

const FLAG_LABELS = { close: "Close to", disagrees: "Disagrees" };

function IdeaCard({ book, idea }) {
  const dismiss = (state) => send("POST", `/api/notes/ideas/${idea.id}/state`, { state });
  const written = book.written.find((w) => w.id === idea.note_id);
  const meta = [idea.chapters.slice(0, 2).join(", "), plural(idea.count, "highlight"), idea.cards ? plural(idea.cards, "card") : ""].filter(Boolean).join(" · ");
  return html`<article class="nt-idea">
    <div class="nt-idea-l">
      <div class="rowg" style="align-items:baseline"><h2>${idea.title}</h2><span class="nt-small">${meta}</span></div>
      ${idea.quote && html`<p class="nt-quote">“${idea.quote}”</p>`}
      ${idea.summary && html`<p class="nt-muted">${idea.summary}</p>`}
      ${idea.mine.length > 0 &&
      html`<div class="nt-mine"><span class="lab">Your words</span>${idea.mine.map((m) => html`<span>${m}</span>`)}</div>`}
      ${idea.flags.map(
        (f) => html`<div class=${`nt-flag ${f.kind}`}><b>${FLAG_LABELS[f.kind]}</b><span>[[${f.note}]] ${f.why}</span></div>`,
      )}
    </div>
    <div class="nt-idea-r">
      ${idea.talks.length > 0 &&
      html`<span class="lab">Talks to in your slip-box</span>
        <div class="nt-chips">${idea.talks.slice(0, 5).map((t) => html`<span class="nt-chip">${t}</span>`)}${idea.talks.length > 5 && html`<span class="nt-chip">+${idea.talks.length - 5}</span>`}</div>`}
      <div class="rowg" style="margin-top:auto">
        ${idea.state === "written" &&
        html`<span class="nt-done"><span class="dot"></span>Written as [[${idea.note}]]</span>
          ${written && html`<button class="lnk" onClick=${() => go(notesPath("writing", book.id, written.id))}>Open</button>`}`}
        ${idea.state === "todo" &&
        html`<button class="btn pri" onClick=${() => go(notesPath("idea", book.id, idea.id))}>Write</button>
          <button class="btn" onClick=${() => dismiss("dismissed")}>Dismiss</button>`}
        ${idea.state === "dismissed" && html`<button class="btn" onClick=${() => dismiss("todo")}>Bring back</button>`}
      </div>
    </div>
  </article>`;
}

function Stats({ book, landed }) {
  const s = book.stats;
  const rows = landed
    ? [[s.notes, s.notes === 1 ? "note in your own words" : "notes in your own words"], [s.links, s.links === 1 ? "note now leads to them" : "notes now lead to them"], [s.books, s.books === 1 ? "other book they talk to" : "other books they talk to"], [s.todo, s.todo === 1 ? "idea still to look at" : "ideas still to look at"]]
    : [[s.notes, s.notes === 1 ? "note written" : "notes written"], [s.links, s.links === 1 ? "link made" : "links made"], [s.books, s.books === 1 ? "other book it talks to" : "other books it talks to"], [s.todo, s.todo === 1 ? "idea to look at" : "ideas to look at"]];
  return html`<div class="nt-stats">${rows.map(([n, label]) => html`<div class="nt-stat"><b>${n}</b><span>${label}</span></div>`)}</div>`;
}

function Ideas({ id }) {
  const { data: book, error } = useNotes(`/api/notes/books/${encodeURIComponent(id)}`);
  const [filter, setFilter] = useState("todo");
  useTick();
  if (!book) return html`<${NotesTop} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Opening the book<//>`}</div></div>`;
  const counts = { todo: 0, written: 0, dismissed: 0 };
  book.ideas.forEach((i) => (counts[i.state] += 1));
  const shown = book.ideas.filter((i) => i.state === filter);
  const gathering = book.status === "gathering";
  return html`
    <${NotesTop} book=${book} tab="ideas" />
    <div class="nt-page"><div class="nt-wrap nt-mid">
      <div class="nt-cols" style="justify-content:space-between">
        <div class="nt-head" style="flex:1 1 420px">
          <h1 class="h1">What did you just read?</h1>
          <p class="lede">${book.ideas.length
            ? `Your ${plural(book.highlight_count, "highlight")}, gathered into ${plural(book.ideas.length, "idea")}. Most won't need a note. Look for the few that change how you think.`
            : `Your ${plural(book.highlight_count, "highlight")}, waiting to be gathered into ideas.`}</p>
        </div>
      </div>
      <${Stats} book=${book} />
      <div class="nt-bar">
        <div class="seg">
          ${[["todo", "To look at"], ["written", "Written"], ["dismissed", "Dismissed"]].map(
            ([key, label]) => html`<button key=${key} class=${filter === key ? "sg on" : "sg"} onClick=${() => setFilter(key)}>${label} ${counts[key]}</button>`,
          )}
        </div>
        ${gathering
          ? html`<${Running}>Gathering your highlights into ideas. This takes a few minutes.<//>`
          : html`<span class="nt-small">${book.gathered_at ? `Grouped ${ago(book.gathered_at)} ·` : ""}</span>
            <button class="btn" onClick=${() => gather(id)} disabled=${!book.vault.ok}>${book.ideas.length ? "Re-process book" : "Gather ideas"}</button>`}
      </div>
      ${book.status === "failed" && html`<div class="banner"><span>${book.error}</span><button class="btn" onClick=${() => gather(id)}>Try again</button></div>`}
      <div class="nt-ideas" style="border-top:3px solid var(--ink)">
        ${shown.map((idea) => html`<${IdeaCard} key=${idea.id} book=${book} idea=${idea} />`)}
        ${!shown.length && !gathering && html`<div class="empty" style="padding:20px 8px">${filter === "todo" && book.ideas.length ? "Nothing left to look at. See where the book landed." : filter === "todo" ? "No ideas yet." : "None yet."}</div>`}
      </div>
      ${book.missed.length > 0 && html`<p class="nt-small">The Book Companion has ${plural(book.missed.length, "idea")} you didn't highlight. <button class="lnk" onClick=${() => go(notesPath("missed", id))}>Look at them</button></p>`}
    </div></div>
  `;
}

// What wasn't highlighted

function Missed({ id }) {
  const { data: book, error } = useNotes(`/api/notes/books/${encodeURIComponent(id)}`);
  if (!book) return html`<${NotesTop} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Opening the book<//>`}</div></div>`;
  const counts = { todo: 0, written: 0, dismissed: 0 };
  book.missed.forEach((i) => (counts[i.state] += 1));
  const shown = book.missed.filter((i) => i.state === "todo");
  const dismiss = (idea) => send("POST", `/api/notes/ideas/${idea.id}/state`, { state: "dismissed" });
  return html`
    <${NotesTop} book=${book} tab="missed" />
    <div class="nt-page"><div class="nt-wrap nt-mid">
      <div class="nt-cols">
        <div class="nt-main" style="gap:20px">
          <div class="nt-head">
            <h1 class="h1">Why didn't you highlight these?</h1>
            <p class="lede" style="max-width:62ch">${book.missed.length
              ? `The Book Companion found ${plural(book.missed.length, "key idea")} in parts you read but didn't mark. Maybe you knew it already, skimmed past it, or didn't buy it. A disagreement is often worth a note.`
              : book.status === "gathering"
                ? "The AI is still reading. Ideas you didn't highlight show here when it's done."
                : "Nothing here yet. These come from the book's Companion, when it has one."}</p>
          </div>
          <div class="nt-ideas" style="border-top:3px solid var(--ink)">
            ${shown.map(
              (i) => html`<article key=${i.id} class="nt-idea" style="grid-template-columns:minmax(0,1fr)">
                <div class="nt-idea-l">
                  <div class="rowg" style="align-items:baseline"><h2>${i.title}</h2><span class="nt-small">${i.chapter}</span></div>
                  ${i.quote && html`<p class="nt-quote">“${i.quote}”</p>`}
                  ${i.summary && html`<p class="nt-muted">${i.summary}</p>`}
                  ${i.flags.map((f) => html`<div class=${`nt-flag ${f.kind}`}><b>${f.kind === "disagrees" ? "Disagrees with" : "Close to"}</b><span>[[${f.note}]] ${f.why}</span></div>`)}
                  <div class="rowg" style="margin-top:4px">
                    <button class="btn pri" onClick=${() => go(notesPath("idea", id, i.id))}>Write</button>
                    <button class="btn" onClick=${() => dismiss(i)}>Dismiss</button>
                  </div>
                </div>
              </article>`,
            )}
            ${!shown.length && book.missed.length > 0 && html`<div class="empty" style="padding:20px 8px">You've been through them all.</div>`}
          </div>
        </div>
        <aside class="nt-side nt-sec" style="max-width:340px;gap:14px">
          <h2 class="nt-h2">So far</h2>
          <div class="nt-tally">
            <div><span>Written</span><b>${counts.written}</b></div>
            <div><span>Dismissed</span><b>${counts.dismissed}</b></div>
            <div><span>To look at</span><b>${counts.todo}</b></div>
          </div>
          <p class="nt-muted">Write opens the idea the same way as one you highlighted, with the book's passage in place of a highlight.</p>
        </aside>
      </div>
    </div></div>
  `;
}

// Where it landed

function bySource(linked) {
  const groups = new Map();
  for (const link of linked) {
    const name = link.same_book ? "This book" : link.book ? link.book.replace(/^#book\//, "") : "Your own notes";
    if (!groups.has(name)) groups.set(name, []);
    groups.get(name).push(link.title);
  }
  return [...groups.entries()];
}

function Landed({ id }) {
  const { data: book, error } = useNotes(`/api/notes/books/${encodeURIComponent(id)}`);
  if (!book) return html`<${NotesTop} /><div class="center"><div class="narrow">${error ? html`<p class="lede">${error}</p>` : html`<${Running}>Opening the book<//>`}</div></div>`;
  const hub = () => send("POST", `/api/notes/books/${encodeURIComponent(id)}/hub`);
  return html`
    <${NotesTop} book=${book} tab="landed" />
    <div class="nt-page"><div class="nt-wrap nt-mid" style="gap:26px">
      <div class="nt-head">
        <h1 class="h1">Where ${book.title} landed</h1>
        <p class="lede" style="max-width:64ch">What this book added to what you already knew, and where it now sits.</p>
      </div>
      <${Stats} book=${book} landed />
      <div class="nt-cols" style="gap:28px">
        <section class="nt-main">
          <h2 class="nt-h2">The web around it</h2>
          ${book.written.map(
            (note) => html`<div key=${note.id} class="nt-web">
              <div class="nt-web-src" style=${`grid-row: span ${Math.max(1, bySource(note.linked).length)}`}>
                <span class="lab">${note.added ? "Added to" : "From this book"}</span>
                <button class="nt-me" onClick=${() => go(notesPath("writing", id, note.id))}>${note.title}</button>
                <span class="nt-small">${plural(note.linked.length, "link")}${note.done ? "" : " · still writing"}</span>
              </div>
              ${bySource(note.linked).map(
                ([src, titles]) => html`<div class="nt-web-row"><span>${src}</span><div class="nt-chips">${titles.map((t) => html`<span class="nt-chip">${t}</span>`)}</div></div>`,
              )}
              ${!note.linked.length && html`<div class="nt-web-row"><span>No links yet</span></div>`}
            </div>`,
          )}
          ${!book.written.length && html`<div class="empty">No notes yet. <button class="lnk" onClick=${() => go(notesPath("ideas", id))}>Go to the ideas</button></div>`}
          <p class="nt-muted">Every note you write from this book adds a row. The book becomes part of how your slip-box connects, not a pile of its own.</p>
        </section>
        <section class="nt-side" style="gap:12px">
          <h2 class="nt-h2">The book's own note</h2>
          <p class="nt-muted">First what you got out of it without looking at your notes, then your notes.</p>
          <pre class="nt-pre">${book.hub.preview}</pre>
          <span class="nt-small">${book.hub.title}.md · ${book.hub.exists ? "in your slip-box" : "not created yet"}</span>
          <div class="rowg"><button class="btn pri" onClick=${hub} disabled=${!book.vault.ok}>${book.hub.exists ? "Update and open in Obsidian" : "Create and open in Obsidian"}</button></div>
          ${!book.reflection.trim() && html`<span class="nt-small">You haven't written what you got out of it yet. <button class="lnk" onClick=${() => go(notesPath("think", id))}>Write it now</button></span>`}
        </section>
      </div>
    </div></div>
  `;
}

// The router for #/notes/<screen>/<book>/<item>

export function Notes() {
  const { route } = useStore();
  const screen = route.sub || "setup";
  const book = route.notesBook;
  const item = route.item;
  if (screen === "books") return html`<${Books} />`;
  if (book && screen === "think") return html`<${Think} key=${book} id=${book} />`;
  if (book && screen === "ideas") return html`<${Ideas} key=${book} id=${book} />`;
  if (book && screen === "missed") return html`<${Missed} key=${book} id=${book} />`;
  if (book && screen === "landed") return html`<${Landed} key=${book} id=${book} />`;
  if (book && item && screen === "idea") return html`<${Gather} key=${item} book=${book} id=${item} />`;
  if (book && item && screen === "writing") return html`<${Writing} key=${item} book=${book} id=${item} />`;
  return html`<${Setup} />`;
}
