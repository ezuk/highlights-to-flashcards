// Connect: Readwise, AnkiWeb and the Calibre library, used by the first screen and Settings.

import { useState } from "../../vendor/preact-htm.js";
import { useStore, go, openBook, syncNow, saveReadwiseToken, ankiwebLogin, ankiwebLogout, chooseCalibreLibrary, showError, clearError } from "../store.js";
import { html, ErrorBar, Pulse, plural } from "../ui.js";

// Runs `work`, showing its error in the error bar. Answers whether it worked.
async function attempt(setBusy, work) {
  clearError();
  setBusy(true);
  try {
    await work();
    return true;
  } catch (error) {
    showError(error);
    return false;
  } finally {
    setBusy(false);
  }
}

const Row = ({ ok, title, detail, children }) => html`<div class=${ok ? "chk" : "chk warn"}>
  <span class="chkg">${ok ? "✓" : "·"}</span>
  <span class="chkt">${title}</span>
  ${detail && html`<span class="chkp">${detail}</span>`}
  ${children && html`<div style="grid-column:2;display:flex;flex-direction:column;gap:8px;margin-top:6px">${children}</div>`}
</div>`;

function Readwise({ source }) {
  const [token, setToken] = useState("");
  const [busy, setBusy] = useState(false);
  const [changing, setChanging] = useState(false);
  const save = async (event) => {
    event.preventDefault();
    if (await attempt(setBusy, () => saveReadwiseToken(token))) {
      setToken("");
      setChanging(false);
    }
  };
  const detail = source.error
    || (source.connected
      ? `${plural(source.books, "book")}, ${plural(source.highlights, "highlight")}${source.synced ? `. Synced ${source.synced}` : ""}`
      : "Your highlights come from Readwise. Paste an access token from readwise.io/access_token.");
  return html`<${Row} ok=${source.connected && !source.error} title="Readwise" detail=${detail}>
    ${!source.connected || changing
      ? html`<form class="rowg" onSubmit=${save}>
          <input class="in" style="flex:1 1 240px;width:auto" type="password" autocomplete="off" placeholder="Access token"
            value=${token} onInput=${(e) => setToken(e.target.value)} />
          <button class="btn pri" type="submit" disabled=${busy || !token.trim()}>${busy ? "Checking..." : "Save token"}</button>
          ${changing && html`<button class="lnk" type="button" onClick=${() => setChanging(false)}>Cancel</button>`}
        </form>`
      : html`<div class="rowg"><button class="lnk" onClick=${() => setChanging(true)}>Change token</button>
          <button class="lnk" onClick=${() => attempt(setBusy, () => saveReadwiseToken(""))} disabled=${busy}>Forget token</button></div>`}
  <//>`;
}

function AnkiWeb({ source }) {
  const [email, setEmail] = useState("");
  const [password, setPassword] = useState("");
  const [busy, setBusy] = useState(false);
  const logIn = async (event) => {
    event.preventDefault();
    if (await attempt(setBusy, () => ankiwebLogin(email, password))) setPassword("");
  };
  const waiting = source.unsynced ? `. ${plural(source.unsynced, "change")} to send` : "";
  const detail = source.error
    || (source.connected
      ? (source.synced ? `Synced ${source.synced}${waiting}` : `Logged in, not synced yet${waiting}`)
      : "New cards and fixes go to your Anki collection through AnkiWeb. Your password is used once to log in and isn't kept.");
  return html`<${Row} ok=${source.connected && !source.error} title="AnkiWeb" detail=${detail}>
    ${source.connected
      ? html`<div class="rowg"><button class="lnk" onClick=${() => attempt(setBusy, ankiwebLogout)} disabled=${busy}>Log out</button></div>`
      : html`<form class="rowg" onSubmit=${logIn}>
          <input class="in" style="flex:1 1 200px;width:auto" type="email" autocomplete="username" placeholder="Email"
            value=${email} onInput=${(e) => setEmail(e.target.value)} />
          <input class="in" style="flex:1 1 160px;width:auto" type="password" autocomplete="current-password" placeholder="Password"
            value=${password} onInput=${(e) => setPassword(e.target.value)} />
          <button class="btn pri" type="submit" disabled=${busy || !email.trim() || !password}>${busy ? "Logging in..." : "Log in"}</button>
        </form>`}
  <//>`;
}

function Calibre({ source }) {
  const [busy, setBusy] = useState(false);
  const ok = !source.error && source.books > 0;
  const detail = source.error
    || (source.library ? `${source.library}. ${plural(source.with_epub, "ePub")}` : "Choose your Calibre library, so the AI can read the books around your highlights.");
  return html`<${Row} ok=${ok} title="Calibre library" detail=${detail}>
    <div class="rowg"><button class="btn" onClick=${() => attempt(setBusy, chooseCalibreLibrary)} disabled=${busy}>${source.library ? "Choose another folder" : "Choose folder"}</button></div>
  <//>`;
}

// The three sources and a Sync now button.
export function Sources() {
  const { state } = useStore();
  const [busy, setBusy] = useState(false);
  const { sources } = state;
  const canSync = sources.readwise.connected || sources.ankiweb.connected;
  return html`<div>
    <div style="border-top:3px solid var(--ink)">
      <${Readwise} source=${sources.readwise} />
      <${AnkiWeb} source=${sources.ankiweb} />
      <${Calibre} source=${sources.calibre} />
    </div>
    <div class="rowg" style="margin-top:12px">
      ${sources.syncing
        ? html`<span class="rowg"><${Pulse} />Syncing Readwise and AnkiWeb...</span>`
        : html`<button class="btn" onClick=${() => attempt(setBusy, () => syncNow())} disabled=${busy || !canSync}>Sync now</button>`}
    </div>
  </div>`;
}

export function Connect() {
  const { state, ui } = useStore();
  const { sources, resume } = state;
  return html`
    <${ErrorBar} />
    <div class="center"><div class="narrow" style="gap:10px">
      <h1 class="h1">Highlights to Flashcards</h1>
      <p class="lede">Turns your Readwise highlights into Anki cards, reading the book itself from Calibre. Connect these once and they stay connected.</p>
      <${Sources} />
      <div class="rowg" style="margin-top:12px">
        <button class="btn pri" onClick=${() => go("#/start")} disabled=${!sources.ready}>Continue</button>
      </div>
      ${resume && html`<div class="resume">
        <div><div style="font-weight:500" class="rowg">${resume.text}</div><div style="font-size:13.5px;color:var(--ink2)">${resume.detail}</div></div>
        <button class="btn" onClick=${() => openBook(resume.book_id, "highlights")} disabled=${!!ui.opening}>Keep working</button>
      </div>`}
    </div></div>
  `;
}
