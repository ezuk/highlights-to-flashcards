#!/usr/bin/env python3
"""Runs the app against fake Readwise, Calibre and AnkiWeb in a throwaway folder, for trying the
screens: .venv/bin/python tests/dev_server.py [--port N] [--keep FOLDER]

The fake Readwise token is `test-token`. Any email logs in to the fake AnkiWeb with the password
`test-password`. Nothing leaves this computer.
"""

import argparse
import json
import os
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
sys.path.insert(0, str(ROOT))
sys.path.insert(0, str(ROOT / "tests"))

import fakes  # noqa: E402


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("--port", default="8791")
    parser.add_argument("--keep", help="folder to keep the data in, instead of a throwaway one")
    parser.add_argument("--connected", action="store_true", help="start with Readwise and AnkiWeb connected")
    args = parser.parse_args()

    home = Path(args.keep) if args.keep else Path(tempfile.mkdtemp(prefix="h2f-dev-"))
    home.mkdir(parents=True, exist_ok=True)
    library = home / "Calibre Library"
    if not (library / "metadata.db").exists():
        fakes.make_calibre_library(library)
    secrets = home / "secrets.json"
    if args.connected and not secrets.exists():
        secrets.write_text(json.dumps({"readwise": {"token": fakes.TOKEN},
                                       "ankiweb": {"hkey": "test-hkey", "endpoint": ""}}))

    readwise = fakes.FakeReadwise().__enter__()
    os.environ.update({
        "H2F_HOME": str(home / "data"),
        "H2F_SECRETS_FILE": str(secrets),
        "H2F_READWISE_URL": readwise.url,
        "H2F_CALIBRE_LIBRARY": str(library),
        "H2F_CLAUDE_BIN": str(ROOT / "tests" / "fixtures" / "fake_ai" / "claude"),
        "H2F_CODEX_BIN": str(ROOT / "tests" / "fixtures" / "fake_ai" / "codex"),
    })

    from h2f import collection

    fakes.fake_ankiweb(collection.Collection)
    print(f"Data folder: {home}", flush=True)
    sys.path.insert(0, str(ROOT))
    import run

    sys.exit(run.main(["--no-open", "--port", args.port]))


if __name__ == "__main__":
    main()
