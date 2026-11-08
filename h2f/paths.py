"""Where the app keeps its files, with environment overrides for tests."""

import os
from pathlib import Path

APP_NAME = "Highlights to Flashcards"


def repo_root():
    return Path(__file__).resolve().parents[1]


def home():
    override = os.environ.get("H2F_HOME")
    if override:
        return Path(override)
    return Path.home() / "Library" / "Application Support" / APP_NAME


def db_path():
    return home() / "h2f.db"


def collection_path():
    return home() / "collection" / "collection.anki2"


def prompts_dir():
    return home() / "prompts"


def companions_dir():
    return home() / "companions"


def companion_work_dir():
    return home() / "companion-work"


def book_text_dir():
    return home() / "book-text"


def readwise_url():
    return os.environ.get("H2F_READWISE_URL", "https://readwise.io").rstrip("/")


def secrets_file():
    """A JSON file used in place of the Keychain, for tests."""
    override = os.environ.get("H2F_SECRETS_FILE")
    return Path(override) if override else None


def companion_command_dir():
    """Folder the Book Companion command runs in, when one is set up."""
    override = os.environ.get("H2F_COMPANION_DIR")
    return Path(override) if override else None
