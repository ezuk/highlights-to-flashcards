"""Slip-box notes: reading an Obsidian vault, building note files and adding links.

The vault is read as plain Markdown. New notes are written as files. Other notes are changed
through Obsidian's Advanced URI plugin while Obsidian runs, so the change goes through its
editor, and written directly when it doesn't.
"""

import os
import re
import subprocess
import time
import unicodedata
from pathlib import Path
from urllib.parse import quote

SKIPPED_FOLDERS = {"Templates"}
INDEX_SNIPPET = 260
HUB_SUFFIX = " (book)"
HUB_FIRST = "What I got out of it, without looking at my notes first:"
HUB_NOTES = "Now going over my notes:"
SEE_ALSO = "See also:"
URI_WAIT_SECONDS = 8
URI_POLL_SECONDS = 0.2
NEW_NOTE_SECONDS = 1.0

_LINK_RE = re.compile(r"\[\[([^\[\]\n]+?)\]\]")
_TAG_RE = re.compile(r"(?<![\w/#])#([A-Za-z][\w/-]*)")
_FRONT_RE = re.compile(r"\A---\n.*?\n---\n", re.S)
_BAD_TITLE = re.compile(r'[\\/:*?"<>|#^\[\]\x00-\x1f]')
_SPACES = re.compile(r"\s+")
_SEE_ALSO_RE = re.compile(r"^see also:?[ \t]*(.*)$", re.I | re.M)
_PROTECTED_RE = re.compile(r"\[\[[^\]\n]*\]\]|`[^`\n]*`|\[[^\]\n]*\]\([^)\n]*\)|https?://\S+|#[\w/-]+")


class VaultError(Exception):
    """The vault or a note can't be used. The message is shown to the user."""


# Names


def book_tag(title):
    """`#book/ArtOfWar` from "The Art of War: The Oldest Military Treatise"."""
    main = (title or "").split(":")[0]
    main = re.sub(r"^\s*(the|a|an)\s+", "", main, flags=re.I)
    words = re.findall(r"[^\W_]+", unicodedata.normalize("NFC", main))
    name = "".join(w[:1].upper() + w[1:] for w in words)
    return f"#book/{name}" if name else "#book/Untitled"


def clean_tag(text):
    """A typed tag as the vault writes it, or None when it can't be one."""
    text = (text or "").strip()
    if not text.startswith("#"):
        text = "#" + text
    return text if re.fullmatch(r"#[^\W\d_][\w/-]*", text) else None


def clean_title(text):
    """A note title that works as a file name and a link, or "" when nothing is left."""
    text = _BAD_TITLE.sub(" ", unicodedata.normalize("NFC", text or ""))
    return _SPACES.sub(" ", text).strip().strip(".").strip()


def hub_title(book_title):
    main = clean_title((book_title or "").split(":")[0]) or "Untitled"
    return main + HUB_SUFFIX


# Reading


def _body(text):
    return _FRONT_RE.sub("", text.replace("\r\n", "\n"), count=1)


def links_in(text):
    """Link targets in `text`, in order, each once, without aliases or headings."""
    found = []
    for match in _LINK_RE.finditer(text or ""):
        target = match.group(1).split("|")[0].split("#")[0].strip()
        if target and target.lower() not in {t.lower() for t in found}:
            found.append(target)
    return found


def tags_in(text):
    return sorted({"#" + t for t in _TAG_RE.findall(_body(text or ""))})


def snippet(text, limit=INDEX_SNIPPET):
    """The note's words for an index line: no tags, quotes, link brackets or extra space."""
    lines = []
    for line in _body(text).split("\n"):
        stripped = line.strip()
        if not stripped or stripped.startswith(">") or re.fullmatch(r"(#[\w/-]+\s*)+", stripped):
            continue
        lines.append(stripped)
    plain = " ".join(lines)
    plain = _LINK_RE.sub(lambda m: m.group(1).split("|")[-1], plain)
    plain = re.sub(r"[*_`]+", "", plain)
    plain = _SPACES.sub(" ", plain).strip()
    return plain if len(plain) <= limit else plain[:limit].rsplit(" ", 1)[0] + "…"


class Vault:
    """One Obsidian vault folder. `launch(args)` runs `open`; `obsidian_running()` says whether
    Obsidian is open. Both are replaceable in tests."""

    def __init__(self, root, launch=None, obsidian_running=None, sleep=time.sleep):
        self.root = Path(root).expanduser()
        self._launch = launch or _open
        self._running = obsidian_running or _obsidian_running
        self._sleep = sleep

    @property
    def name(self):
        return self.root.name

    def check(self):
        """{ok, notes, advanced_uri, git, problem} for the settings screen."""
        if not self.root.is_dir():
            return {"ok": False, "notes": 0, "advanced_uri": False, "git": False,
                    "problem": "That folder isn't there."}
        if not (self.root / ".obsidian").is_dir():
            return {"ok": False, "notes": 0, "advanced_uri": False, "git": False,
                    "problem": "That folder isn't an Obsidian vault. It has no .obsidian folder."}
        plugins = self.root / ".obsidian" / "community-plugins.json"
        try:
            uri = "obsidian-advanced-uri" in plugins.read_text(encoding="utf-8")
        except OSError:
            uri = False
        return {"ok": True, "notes": len(self.paths()), "advanced_uri": uri,
                "git": (self.root / ".git").exists(), "problem": None}

    def paths(self):
        found = []
        for folder, dirs, files in os.walk(self.root):
            here = Path(folder)
            dirs[:] = sorted(d for d in dirs if not d.startswith(".")
                             and not (here == self.root and d in SKIPPED_FOLDERS))
            found.extend(here / f for f in sorted(files) if f.endswith(".md") and not f.startswith("."))
        return found

    def notes(self):
        """Every note: {title, path (inside the vault), text, tags, mtime}."""
        found = []
        for path in self.paths():
            try:
                text = path.read_text(encoding="utf-8", errors="replace")
                mtime = path.stat().st_mtime
            except OSError:
                continue
            found.append({"title": path.stem, "path": path.relative_to(self.root).as_posix(),
                          "text": text, "tags": tags_in(text), "mtime": mtime})
        return found

    def full(self, rel):
        path = (self.root / rel).resolve()
        if self.root.resolve() not in path.parents:
            raise VaultError("That note isn't in the vault.")
        return path

    def read(self, rel):
        try:
            return self.full(rel).read_text(encoding="utf-8", errors="replace")
        except FileNotFoundError:
            raise VaultError(f"“{Path(rel).stem}” isn't in the vault any more. It may have been renamed.") from None
        except OSError:
            raise VaultError(f"“{Path(rel).stem}” couldn't be read.") from None

    def find(self, title):
        """A note's path by its title, ignoring case, or None."""
        wanted = title.strip().lower()
        for path in self.paths():
            if path.stem.lower() == wanted:
                return path.relative_to(self.root).as_posix()
        return None

    # Writing

    def create(self, title, text):
        """Writes a new note at the vault's top level. Refuses a title already used."""
        title = clean_title(title)
        if not title:
            raise VaultError("The note needs a title.")
        if self.find(title):
            raise VaultError(f"There's already a note called “{title}”. Pick another title, or add to that note.")
        path = self.root / f"{title}.md"
        try:
            with open(path, "x", encoding="utf-8") as f:
                f.write(text)
        except FileExistsError:
            raise VaultError(f"There's already a note called “{title}”.") from None
        except OSError:
            raise VaultError("The note couldn't be written to the vault.") from None
        return path.relative_to(self.root).as_posix()

    def change(self, rel, before, after):
        """Replaces a note's text `before` with `after`. Through Obsidian when it runs, so its
        editor makes the change, else on disk. Refuses when the note changed since it was read."""
        if before == after:
            return False
        if self.read(rel) != before:
            raise VaultError(f"“{Path(rel).stem}” changed while the app was working. Try again.")
        if self._running():
            self._launch(["open", "-g", self.uri("advanced-uri", filepath=rel, data=after,
                                                 mode="overwrite", openmode="silent")])
            waited = 0.0
            while waited < URI_WAIT_SECONDS:
                if self.read(rel) == after:
                    return True
                self._sleep(URI_POLL_SECONDS)
                waited += URI_POLL_SECONDS
            raise VaultError(f"Obsidian didn't make the change to “{Path(rel).stem}”. Check the Advanced URI "
                             "plugin is on, then try again.")
        try:
            self.full(rel).write_text(after, encoding="utf-8")
        except OSError:
            raise VaultError(f"“{Path(rel).stem}” couldn't be changed.") from None
        return True

    def uri(self, action, **params):
        query = "&".join(f"{k}={quote(str(v), safe='')}" for k, v in {"vault": self.name, **params}.items())
        return f"obsidian://{action}?{query}"

    def open_note(self, rel, new=False):
        """Opens a note in Obsidian. A `new` one waits a moment, so Obsidian has seen the file."""
        if new and self._running():
            self._sleep(NEW_NOTE_SECONDS)
        return self._launch(["open", self.uri("open", file=rel[:-3] if rel.endswith(".md") else rel)])


def _open(args):
    override = os.environ.get("H2F_OPEN_CMD")
    command = [*override.split(), *args[1:]] if override else args
    try:
        return subprocess.run(command, capture_output=True, timeout=30, check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


def _obsidian_running():
    if os.environ.get("H2F_OPEN_CMD"):
        return False
    try:
        return subprocess.run(["pgrep", "-x", "Obsidian"], capture_output=True, timeout=5,
                              check=False).returncode == 0
    except (OSError, subprocess.SubprocessError):
        return False


# Building notes


def new_note(tag, quotes, see_also=()):
    """A new note: the book's tag, the highlights as quotes, room to write and its links."""
    parts = [tag] if tag else []
    parts.extend("\n".join("> " + line if line else ">" for line in q.strip().split("\n")) for q in quotes if q.strip())
    text = "\n\n".join(parts) + "\n\n\n"
    if see_also:
        text += "\n" + SEE_ALSO + " " + ", ".join(f"[[{t}]]" for t in see_also) + "\n"
    return text


def append_quotes(text, quotes):
    """`text` with the quotes added at the end, before a See also line when there is one."""
    block = "\n\n".join("\n".join("> " + line if line else ">" for line in q.strip().split("\n"))
                        for q in quotes if q.strip())
    if not block:
        return text
    match = _SEE_ALSO_RE.search(text)
    if match:
        head = text[:match.start()].rstrip("\n")
        return head + "\n\n" + block + "\n\n" + text[match.start():]
    return text.rstrip("\n") + "\n\n" + block + "\n"


def _has_link(text, title):
    return title.lower() in {t.lower() for t in links_in(text)}


def _first_mention(text, title):
    # The first place the title is written as whole words, outside links, code, tags,
    # front matter and quoted lines
    front = _FRONT_RE.match(text)
    start = front.end() if front else 0
    pattern = re.compile(r"(?<![\w\[])" + r"\s+".join(map(re.escape, title.split())) + r"(?![\w\]])", re.I)
    offset = start
    for line in text[start:].split("\n"):
        if not line.lstrip().startswith(">"):
            blocked = [m.span() for m in _PROTECTED_RE.finditer(line)]
            for match in pattern.finditer(line):
                if not any(a < match.end() and match.start() < b for a, b in blocked):
                    return offset + match.start(), offset + match.end()
        offset += len(line) + 1
    return None


def add_link(text, title):
    """`text` linking to `title`: its first mention becomes a link, else it joins See also.
    Unchanged when the note already links there."""
    if _has_link(text, title):
        return text
    found = _first_mention(text, title)
    if found:
        a, b = found
        written = text[a:b]
        link = f"[[{written}]]" if " ".join(written.split()) == written else f"[[{title}|{written}]]"
        return text[:a] + link + text[b:]
    match = _SEE_ALSO_RE.search(text)
    if match:
        line = match.group(0).rstrip()
        joined = line + (", " if match.group(1).strip() else " ") + f"[[{title}]]"
        return text[:match.start()] + joined + text[match.end():]
    return text.rstrip("\n") + f"\n\n{SEE_ALSO} [[{title}]]\n"


def hub_note(tag, intro, reflection, titles):
    """A book's hub note, in the shape of the user's own."""
    parts = [tag] if tag else []
    if intro:
        parts.append(intro.strip())
    if reflection.strip():
        parts.append(HUB_FIRST + "\n\n" + reflection.strip())
    if titles:
        parts.append(HUB_NOTES + "\n\n" + "\n".join(f"- [[{t}]]" for t in titles))
    return "\n\n".join(parts) + "\n"


def hub_add(text, titles):
    """An existing hub note with the notes it doesn't list yet added under its notes list."""
    missing = [t for t in titles if not _has_link(text, t)]
    if not missing:
        return text
    rows = "\n".join(f"- [[{t}]]" for t in missing)
    if HUB_NOTES not in text:
        return text.rstrip("\n") + f"\n\n{HUB_NOTES}\n\n{rows}\n"
    # After the last list line under the heading, before anything that follows the list
    lines = text.split("\n")
    start = next(i for i, line in enumerate(lines) if HUB_NOTES in line)
    end = start + 1
    for i in range(start + 1, len(lines)):
        if re.match(r"\s*[-*] ", lines[i]):
            end = i + 1
        elif lines[i].strip() and end > start + 1:
            break
    if end == start + 1:
        lines[end:end] = ["", rows]
    else:
        lines[end:end] = [rows]
    return "\n".join(lines)
