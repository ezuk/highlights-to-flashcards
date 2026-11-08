"""A throwaway home folder with fake Readwise, Calibre and secrets, for each test."""

import os
import shutil
import tempfile
import unittest
from pathlib import Path

import fakes

ENV = ("H2F_HOME", "H2F_SECRETS_FILE", "H2F_READWISE_URL", "H2F_CALIBRE_LIBRARY", "H2F_CLAUDE_BIN", "H2F_CODEX_BIN")
FAKE_AI = Path(__file__).resolve().parent / "fixtures" / "fake_ai"


class TempHome(unittest.TestCase):
    """Sets the app's folders, secrets and Readwise address to throwaway ones."""

    def setUp(self):
        self.tmp = Path(tempfile.mkdtemp(prefix="h2f-test-"))
        self.saved_env = {name: os.environ.get(name) for name in ENV}
        self.readwise = fakes.FakeReadwise().__enter__()
        self.library = fakes.make_calibre_library(self.tmp / "Calibre Library")
        os.environ.update({
            "H2F_HOME": str(self.tmp / "data"),
            "H2F_SECRETS_FILE": str(self.tmp / "secrets.json"),
            "H2F_READWISE_URL": self.readwise.url,
            "H2F_CALIBRE_LIBRARY": self.library,
            "H2F_CLAUDE_BIN": str(FAKE_AI / "claude"),
            "H2F_CODEX_BIN": str(FAKE_AI / "codex"),
        })

    def tearDown(self):
        self.readwise.__exit__(None, None, None)
        for name, value in self.saved_env.items():
            if value is None:
                os.environ.pop(name, None)
            else:
                os.environ[name] = value
        shutil.rmtree(self.tmp, ignore_errors=True)
