#!/usr/bin/env python3
"""Blocks commits and pushes that would publish personal information.

    privacy_check.py --staged          files about to be committed (pre-commit hook)
    privacy_check.py --push OLD NEW    commits about to be pushed, with their authors (pre-push hook)
    privacy_check.py --all             every tracked file

Generic rules live here. Words that identify you (your name, your email, your
home folder, titles you don't want public) go one per line in
`.git/info/private-words`, which git never commits or pushes.
"""

import re
import subprocess
import sys
from pathlib import Path

ROOT = Path(subprocess.run(["git", "rev-parse", "--show-toplevel"], capture_output=True, text=True,
                           check=True).stdout.strip())
PRIVATE_WORDS = ROOT / ".git" / "info" / "private-words"

FIXTURES = "tests/fixtures/"
PLACEHOLDER_USERS = {"someone", "you", "me", "user", "username", "runner", "name", "example", "shared"}
ALLOWED_EMAIL_DOMAINS = ("example.com", "example.org", "example.net", "users.noreply.github.com")

# Files that hold a collection, a library or a book, which never belong in the repo outside test fixtures.
PRIVATE_SUFFIXES = (".anki2", ".anki21", ".apkg", ".colpkg", ".db", ".sqlite", ".sqlite3", ".epub",
                    ".mobi", ".azw", ".azw3", ".kfx", ".pdf", ".companion.md", ".sdr", ".lua.old")
PRIVATE_NAMES = (".env", "secrets.json", "credentials.json", "readwise-token", "ankiweb.json")

HOME_PATH = re.compile(r"(?:/Users|/home)/([A-Za-z0-9._-]+)")
EMAIL = re.compile(r"[A-Za-z0-9._%+-]+@([A-Za-z0-9-]+\.[A-Za-z0-9.-]*[A-Za-z]{2,})")
SECRETS = [
    (re.compile(r"Token\s+[A-Za-z0-9]{30,}"), "a Readwise token"),
    (re.compile(r"""["']?hkey["']?\s*[:=]\s*["'][A-Za-z0-9+/=_-]{12,}"""), "an AnkiWeb sync key"),
    (re.compile(r"-----BEGIN [A-Z ]*PRIVATE KEY-----"), "a private key"),
    (re.compile(r"\bsk-[A-Za-z0-9_-]{20,}"), "an API key"),
    (re.compile(r"\bgh[pousr]_[A-Za-z0-9]{30,}"), "a GitHub token"),
]


def private_words():
    try:
        lines = PRIVATE_WORDS.read_text(encoding="utf-8").splitlines()
    except FileNotFoundError:
        return []
    return [w.strip() for w in lines if w.strip() and not w.lstrip().startswith("#")]


def text_problems(text, words):
    found = []
    for match in HOME_PATH.finditer(text):
        if match.group(1).lower() not in PLACEHOLDER_USERS:
            found.append(f"a home folder path ({match.group(0)})")
    for match in EMAIL.finditer(text):
        if not match.group(1).lower().endswith(ALLOWED_EMAIL_DOMAINS):
            found.append(f"an email address ({match.group(0)})")
    for pattern, what in SECRETS:
        if pattern.search(text):
            found.append(what)
    lowered = text.lower()
    for word in words:
        if word.lower() in lowered:
            found.append(f"a word from .git/info/private-words ({word})")
    return found


def path_problems(path):
    lowered = path.lower()
    if lowered.startswith(FIXTURES):
        return []
    name = lowered.rsplit("/", 1)[-1]
    if lowered.endswith(PRIVATE_SUFFIXES) or name in PRIVATE_NAMES:
        return ["a file type that holds personal data (only allowed under tests/fixtures/)"]
    return []


def git(*args, text=True):
    return subprocess.run(["git", *args], cwd=ROOT, capture_output=True, text=text, check=True).stdout


def check_blob(path, data, words, report):
    problems = path_problems(path) + text_problems(path, words)
    if b"\0" not in data[:8000]:
        problems += text_problems(data.decode("utf-8", errors="replace"), words)
    for problem in dict.fromkeys(problems):
        report.append(f"{path}: {problem}")


def staged(words, report):
    names = git("diff", "--cached", "--name-only", "--diff-filter=ACMR", "-z").split("\0")
    for path in filter(None, names):
        check_blob(path, git("show", f":{path}", text=False), words, report)


def everything(words, report, rev=None):
    args = ["ls-tree", "-r", "-z", "--name-only", rev] if rev else ["ls-files", "-z"]
    for path in filter(None, git(*args).split("\0")):
        data = git("show", f"{rev}:{path}", text=False) if rev else (ROOT / path).read_bytes()
        check_blob(path, data, words, report)


def pushed(old, new, words, report):
    if set(new) == {"0"}:
        return
    known = subprocess.run(["git", "cat-file", "-e", f"{old}^{{commit}}"], cwd=ROOT, capture_output=True)
    span = f"{old}..{new}" if set(old) != {"0"} and known.returncode == 0 else new
    for line in filter(None, git("log", "--format=%H%x1f%an%x1f%ae%x1f%cn%x1f%ce", span).splitlines()):
        sha, *people = line.split("\x1f")
        for kind, name, email in (("author", people[0], people[1]), ("committer", people[2], people[3])):
            if not email.lower().endswith("users.noreply.github.com"):
                report.append(f"commit {sha[:8]}: {kind} email {email} isn't a GitHub no-reply address")
            for problem in text_problems(name, words):
                report.append(f"commit {sha[:8]}: {kind} name has {problem}")
        for problem in text_problems(git("log", "-1", "--format=%B", sha), words):
            report.append(f"commit {sha[:8]}: message has {problem}")
    everything(words, report, rev=new)


def main(argv):
    words = private_words()
    report = []
    if argv[:1] == ["--staged"]:
        staged(words, report)
    elif argv[:1] == ["--push"] and len(argv) == 3:
        pushed(argv[1], argv[2], words, report)
    elif argv[:1] == ["--all"]:
        everything(words, report)
    else:
        sys.exit(__doc__)
    if not words:
        print("privacy check: .git/info/private-words is missing or empty, so only generic rules ran.",
              file=sys.stderr)
    if report:
        print("privacy check failed. This would publish:", file=sys.stderr)
        for line in report:
            print(f"  {line}", file=sys.stderr)
        return 1
    return 0


if __name__ == "__main__":
    sys.exit(main(sys.argv[1:]))
