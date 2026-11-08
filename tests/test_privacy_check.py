"""The guard that keeps personal information out of commits."""

import importlib.util
import unittest
from pathlib import Path

SCRIPT = Path(__file__).resolve().parent.parent / "scripts" / "privacy_check.py"
spec = importlib.util.spec_from_file_location("privacy_check", SCRIPT)
privacy_check = importlib.util.module_from_spec(spec)
spec.loader.exec_module(privacy_check)


# Built at runtime, so this file doesn't trip the check itself
HOME = "/" + "Users/alex"
LINUX_HOME = "/" + "home/alex"
EMAIL = "alex" + "@" + "mail.org"


class PrivacyCheckTest(unittest.TestCase):
    def problems(self, text, words=()):
        return privacy_check.text_problems(text, list(words))

    def test_flags_home_folders_but_not_placeholders(self):
        self.assertTrue(self.problems(f"open {HOME}/Books"))
        self.assertTrue(self.problems(f"{LINUX_HOME}/x"))
        self.assertFalse(self.problems("~/Library and /Users/you/Books"))

    def test_flags_emails_but_not_examples(self):
        self.assertTrue(self.problems(f"write to {EMAIL}"))
        self.assertFalse(self.problems("tester@example.com and 1+x@users.noreply.github.com"))

    def test_flags_secrets(self):
        self.assertTrue(self.problems("Authorization: Token " + "a" * 40))
        self.assertTrue(self.problems('{"hkey": "' + "b" * 20 + '"}'))
        self.assertFalse(self.problems('{"hkey": "test-hkey"}'))

    def test_flags_private_words_in_any_case(self):
        self.assertTrue(self.problems("By ALEX Q", ["Alex Q"]))

    def test_private_file_types_only_in_fixtures(self):
        self.assertTrue(privacy_check.path_problems("collection.anki2"))
        self.assertTrue(privacy_check.path_problems("notes/My Book.companion.md"))
        self.assertTrue(privacy_check.path_problems("secrets.json"))
        self.assertFalse(privacy_check.path_problems("tests/fixtures/books/pride-and-prejudice.epub"))
        self.assertFalse(privacy_check.path_problems("h2f/calibre.py"))


if __name__ == "__main__":
    unittest.main()
