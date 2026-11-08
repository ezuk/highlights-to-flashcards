// Settings drawer.

import { html, CompanionCopy, Drawer, Field, Select } from "../ui.js";
import { useState } from "../../vendor/preact-htm.js";
import { api } from "../api.js";
import { useStore, saveSettings, update, showError, clearError } from "../store.js";
import { Sources } from "../screens/sources.js";

const SettingSelect = ({ label, value, options, name }) => html`<div class="setrow">
  <span>${label}</span>
  <${Select} value=${value} options=${options} onChange=${(next) => saveSettings({ [name]: next })} />
</div>`;

function Assistant({ assistant, all }) {
  const toggle = (event) => saveSettings({ assistants: { ...all, [assistant.id]: event.target.checked } });
  return html`<label class="asst">
    <input type="checkbox" checked=${assistant.on && assistant.found} disabled=${!assistant.found} onChange=${toggle} />
    <span>${assistant.name}</span>
    <span class="assts">${assistant.status}</span>
    <span class="asstp">${assistant.path}</span>
  </label>`;
}

// Which Calibre book the open book reads from, with a search to pick another.
function BookMatch({ book }) {
  const [text, setText] = useState("");
  const [found, setFound] = useState(null);
  const [busy, setBusy] = useState(false);
  const { calibre } = book;
  const search = async (event) => {
    event.preventDefault();
    clearError();
    try {
      setFound((await api.get(`/api/calibre/search?q=${encodeURIComponent(text)}`)).books);
    } catch (error) {
      showError(error);
    }
  };
  const link = async (calibreId) => {
    clearError();
    setBusy(true);
    try {
      update({ book: await api.post(`/api/books/${encodeURIComponent(book.id)}/link`, { calibre_id: calibreId }) });
      setFound(null);
      setText("");
    } catch (error) {
      showError(error);
    } finally {
      setBusy(false);
    }
  };
  return html`<div class="setg">
    <div class="setgt">This book in Calibre</div>
    <div class="assts">${calibre.title
      ? html`${calibre.title}${calibre.author ? ` · ${calibre.author}` : ""}${calibre.picked ? " (your pick)" : ""}`
      : "No matching ePub found in the Calibre library."}</div>
    ${calibre.file && html`<div class="path">${calibre.file}</div>`}
    <form class="rowg" onSubmit=${search}>
      <input class="in" style="flex:1 1 200px;width:auto" placeholder="Search Calibre by title or author" value=${text} onInput=${(e) => setText(e.target.value)} />
      <button class="btn" type="submit" disabled=${!text.trim()}>Search</button>
    </form>
    ${found && (found.length
      ? found.map((b) => html`<div class="setrow" key=${b.id}><span>${b.title}<span class="assts"> · ${b.author}</span></span>
          <button class="btn" onClick=${() => link(b.id)} disabled=${busy || b.id === calibre.id}>${b.id === calibre.id ? "In use" : "Use this"}</button></div>`)
      : html`<div class="setn">No ePub in Calibre matches that.</div>`)}
    ${calibre.picked && html`<div class="rowg"><button class="lnk" onClick=${() => link(null)} disabled=${busy}>Go back to the automatic match</button></div>`}
  </div>`;
}

export function SettingsDrawer() {
  const { state, book, route } = useStore();
  const { settings, files, skill } = state;
  const cards = state.card_defaults || { notetype: "Highlights to Flashcards", deck_parent: "Highlights" };
  const openBook = book && book.id === route.bookId ? book : null;
  return html`<${Drawer} title="Settings">
    <div class="setg">
      <div class="setgt">Sources</div>
      <${Sources} />
    </div>
    <div class="setg">
      <div class="setgt">New cards</div>
      <div class="fl">
        <div class="lab">Note type</div>
        <${Field} tag="input" commit class="in" value=${settings.notetype} placeholder=${cards.notetype} onSave=${(notetype) => saveSettings({ notetype })} />
      </div>
      <div class="fl">
        <div class="lab">Parent deck</div>
        <${Field} tag="input" commit class="in" value=${settings.deck_parent} placeholder=${cards.deck_parent} onSave=${(deck_parent) => saveSettings({ deck_parent })} />
      </div>
      <div class="setn">Each book gets its own deck inside the parent deck. Leave these empty to use ${cards.notetype} and ${cards.deck_parent}.</div>
    </div>
    ${openBook && openBook.calibre && html`<${BookMatch} book=${openBook} />`}
    <div class="setg">
      <div class="setgt">AI</div>
      ${state.assistants.map((a) => html`<${Assistant} key=${a.id} assistant=${a} all=${settings.assistants} />`)}
      <div class="setn">Turn on both and each model menu lists them together, so you can use a different one per job.</div>
      <${SettingSelect} label="Model for highlight cards" name="model" value=${settings.model} options=${state.models} />
      <${SettingSelect} label="Model for connections" name="meta_model" value=${settings.meta_model} options=${state.models} />
    </div>
    <div class="setg">
      <div class="setgt">Book Companion</div>
      ${openBook && html`<div class="assts">${openBook.companion.copy ? html`<${CompanionCopy} book=${openBook} />` : openBook.companion.status}</div>`}
      <${SettingSelect} label="What the AI reads per highlight" name="companion_send" value=${settings.companion_send} options=${state.comp_opts} />
      <div class="setn">Overview + chapter keeps each request small. Connections and key ideas always read the whole Companion.</div>
      <div class="fl">
        <div class="lab">Folder with the Book Companion skill</div>
        <input class="in" value=${files.assistant} readOnly />
      </div>
      ${skill.problem && html`<div class="setn">${skill.problem}</div>`}
      <div class="setn">Creating a Companion always uses Claude Code, because the skill runs there.</div>
    </div>
    <div class="setg">
      <div class="setgt">Files</div>
      <div class="fl"><div class="lab">Prompts folder</div><div class="path">${files.prompts}</div></div>
      <div class="fl"><div class="lab">Backup and drafts</div><div class="path">${files.db}</div></div>
      <div class="fl"><div class="lab">Anki collection copy</div><div class="path">${files.collection}</div></div>
    </div>
  <//>`;
}
