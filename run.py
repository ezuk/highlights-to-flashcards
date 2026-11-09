#!/usr/bin/env python3
"""Starts Highlights to Flashcards: ./run.sh [--port N] [--no-open]"""

import sys

if sys.version_info < (3, 11):
    sys.exit(f"Highlights to Flashcards needs Python 3.11 or newer, and this is Python {sys.version.split()[0]}.")

import argparse
import fcntl
import logging
import os
import signal
import sqlite3
import threading
from pathlib import Path

sys.path.insert(0, str(Path(__file__).resolve().parent))

from h2f import ai, paths, server, service  # noqa: E402
from h2f.store import StoreError  # noqa: E402


def _port(text):
    try:
        port = int(text)
    except ValueError:
        port = -1
    if not 0 <= port <= 65535:
        raise argparse.ArgumentTypeError("it has to be a whole number from 0 to 65535")
    return port


def parse_args(argv=None):
    parser = argparse.ArgumentParser(description="Make Anki flashcards from a book's Readwise highlights.")
    parser.add_argument("--port", type=_port, default=server.DEFAULT_PORT,
                        help=f"port to use, or the next free one (default {server.DEFAULT_PORT})")
    parser.add_argument("--no-open", action="store_true", help="don't open the browser")
    return parser.parse_args(argv)


def _address_line(http, asked):
    taken = f" (port {asked} was taken)" if asked and http.port != asked else ""
    return f"Highlights to Flashcards is running at {http.url}{taken}. Press Ctrl-C to quit."


def _claim_data_folder():
    """This process's hold on the data folder, or None if another copy has it. The system lets go
    however the process ends. The file names the holder's address."""
    paths.home().mkdir(parents=True, exist_ok=True)
    hold = open(paths.home() / "running.lock", "a+", encoding="utf-8")
    try:
        fcntl.flock(hold, fcntl.LOCK_EX | fcntl.LOCK_NB)
    except OSError:
        return None, hold
    return hold, None


def _point_at_first_copy(other, no_open):
    other.seek(0)
    url = other.read().strip()
    other.close()
    print(f"Highlights to Flashcards is already running at {url}." if url else "Highlights to Flashcards is already running.", flush=True)
    if url and not no_open:
        service._open_command(["open", url])


def _note_address(hold, url):
    hold.seek(0)
    hold.truncate()
    hold.write(url)
    hold.flush()


def _say(text):
    # The terminal may be gone by now
    try:
        print(text, flush=True)
    except OSError:
        pass


def _code_version():
    """Modification times of the app's Python files."""
    root = Path(__file__).resolve().parent
    files = [root / "run.py", *sorted((root / "h2f").rglob("*.py"))]
    found = []
    for file in files:
        try:
            found.append((str(file), file.stat().st_mtime_ns))
        except OSError:
            pass
    return found


def _watch_code(stop, restart, every=1.0):
    """Sets `restart` and `stop` once a Python file changes."""
    known = _code_version()
    while not stop.wait(every):
        if _code_version() != known:
            restart.set()
            stop.set()


def _restart(port):
    """Replaces this process with a fresh copy on the same port, keeping its output."""
    script = str(Path(__file__).resolve())
    os.execv(sys.executable, [sys.executable, script, *sys.argv[1:], "--port", str(port), "--no-open"])


def main(argv=None):
    args = parse_args(argv)
    logging.basicConfig(level=logging.WARNING, format="%(levelname)s %(name)s: %(message)s")

    stop = threading.Event()

    def on_signal(_number, _frame):
        if stop.is_set():
            ai.kill_running()
            os._exit(130)
        stop.set()

    signal.signal(signal.SIGINT, on_signal)
    signal.signal(signal.SIGTERM, on_signal)
    signal.signal(signal.SIGHUP, on_signal)

    try:
        hold, other = _claim_data_folder()
    except OSError as e:
        print(f"Highlights to Flashcards couldn't start: {e}", file=sys.stderr)
        return 1
    if other is not None:
        _point_at_first_copy(other, args.no_open)
        return 0

    try:
        app = service.Service()
    except (OSError, sqlite3.Error, StoreError) as e:
        print(f"Highlights to Flashcards couldn't start: {e}", file=sys.stderr)
        return 1
    try:
        http = server.bind(app, args.port)
    except (server.NoFreePort, OSError) as e:
        app.stop(wait=1)
        print(f"Highlights to Flashcards couldn't start: {e}", file=sys.stderr)
        return 1

    http.start()
    _note_address(hold, http.url)
    print(_address_line(http, args.port), flush=True)
    if not args.no_open and not service._open_command(["open", http.url]):
        print("The browser didn't open. Go to the address above.", file=sys.stderr, flush=True)

    restart = threading.Event()
    if os.environ.get("H2F_LIVE_RELOAD"):
        threading.Thread(target=_watch_code, args=(stop, restart), name="h2f-code-watch", daemon=True).start()

    stop.wait()
    if restart.is_set():
        _say("The code changed. Restarting.")
    http.stop(waiting=lambda: _say("Finishing what's under way. Press Ctrl-C again to quit now."))
    hold.close()
    if restart.is_set():
        _restart(http.port)
    return 0


if __name__ == "__main__":
    sys.exit(main())
