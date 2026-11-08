// Pick a book.

import { useEffect, useState } from "../../vendor/preact-htm.js";
import { useStore, openBook, loadBooks, go } from "../store.js";
import { html, Top, Running } from "../ui.js";

function BookRow({ book, opening }) {
  return html`<button class=${book.dim ? "bk dim" : "bk"} onClick=${() => openBook(book.id)} disabled=${book.none || !!opening}>
    <span>
      <span class="bkt" style="display:block">${book.title}</span>
      <span class="bka" style="display:block">${book.author}</span>
      ${book.note && html`<span class="bka" style="display:block">${book.note}</span>`}
      ${book.draft && html`<span class="bkd">${book.draft}</span>`}
    </span>
    <span class="bkm hd">${book.last}</span>
    <span class="bkm hd">${book.read}</span>
    <span class="bkm">${book.hl}</span>
    <span class="bkm hd">${book.comp}</span>
    <span class="bkgo hd">${opening === book.id ? "Opening..." : book.action}</span>
  </button>`;
}

export function PickBook() {
  const { books, state, ui } = useStore();
  const [filter, setFilter] = useState("");

  useEffect(() => {
    loadBooks().catch(() => {});
  }, []);

  let list;
  if (!books) {
    list = html`<${Running}>Reading your highlights<//>`;
  } else if (!books.length) {
    list = html`<div class="empty">${state.sources.syncing
      ? "Getting your highlights from Readwise..."
      : "No book highlights from Readwise yet. Highlight something, let it reach Readwise, then sync."}</div>`;
  } else {
    const words = filter.toLowerCase().split(/\s+/).filter(Boolean);
    const shown = books.filter((b) => words.every((w) => `${b.title} ${b.author}`.toLowerCase().includes(w)));
    list = html`<div>
      <input class="in" style="margin-bottom:12px" placeholder="Find a book" value=${filter} onInput=${(e) => setFilter(e.target.value)} />
      <div class="bkh"><span>Book</span><span>Last highlight</span><span>Read</span><span>Highlights</span><span>Companion</span><span></span></div>
      ${shown.map((book) => html`<${BookRow} key=${book.id} book=${book} opening=${ui.opening} />`)}
      ${!shown.length && html`<div class="empty">No book matches that.</div>`}
    </div>`;
  }

  return html`
    <${Top}><button class="lnk" onClick=${() => go("#/start")}>Back</button><//>
    <div class="center centertop"><div class="wide">
      <div><h1 class="h1">Pick a book</h1><p class="lede" style="margin-top:6px">From your Readwise highlights, most recently highlighted first.</p></div>
      ${list}
    </div></div>
  `;
}
