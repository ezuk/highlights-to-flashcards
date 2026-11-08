"""Readwise, Calibre, the Keychain stand-in, and books built from them."""

import unittest

import fakes
from helpers import TempHome

from h2f import calibre, keychain, library, readwise
from h2f.store import Store, book_id_for


class ReadwiseTest(TempHome):
    def test_token_check(self):
        self.assertTrue(readwise.check_token(fakes.TOKEN))
        self.assertFalse(readwise.check_token("wrong"))

    def test_first_pull_keeps_books_only_and_follows_pages(self):
        store = Store()
        counts = readwise.pull(store, fakes.TOKEN)
        self.assertEqual(counts, {"books": 2, "highlights": 4, "removed": 0})
        self.assertEqual([b["title"] for b in store.readwise_books()], ["Pride and Prejudice", "Meditations"])
        self.assertEqual(sum(1 for path, _q in self.readwise.requests if path == "/api/v2/export/"), 3)

    def test_later_pull_asks_for_changes_and_removes_deleted(self):
        store = Store()
        readwise.pull(store, fakes.TOKEN)
        since = store.get_setting("readwise_pulled_at")
        pride = self.readwise.items[0]
        pride["highlights"][2].update(is_deleted=True, updated_at="2999-01-01T00:00:00Z")
        pride["highlights"].append(fakes._highlight(1004, "She is tolerable; but not handsome enough to tempt me.",
                                                    30, "2999-01-01T00:00:00Z"))
        counts = readwise.pull(store, fakes.TOKEN)
        _path, query = self.readwise.requests[-1]
        self.assertEqual(query.get("updatedAfter"), since)
        self.assertEqual(counts["removed"], 1)
        ids = {h["id"] for h in store.readwise_highlights(fakes.PRIDE_ID)}
        self.assertEqual(ids, {1001, 1002, 1004})

    def test_bad_token_is_a_plain_sentence(self):
        with self.assertRaises(readwise.ReadwiseError) as caught:
            readwise.pull(Store(), "wrong")
        self.assertIn("token", str(caught.exception))


class CalibreTest(TempHome):
    def test_lists_books_with_epubs(self):
        books = {b["id"]: b for b in calibre.list_books(self.library)}
        self.assertTrue(books[1]["epub"].endswith("Pride and Prejudice - Jane Austen.epub"))
        self.assertEqual(books[1]["authors"], ["Jane Austen"])
        self.assertIsNone(books[2]["epub"])

    def test_matches_on_title_and_surname(self):
        books = calibre.list_books(self.library)
        self.assertEqual(calibre.match("Pride and Prejudice: A Novel", "Austen, Jane", books)["id"], 1)
        self.assertIsNone(calibre.match("Meditations", "Marcus Aurelius", books))

    def test_not_a_library(self):
        with self.assertRaises(calibre.CalibreError):
            calibre.list_books(str(self.tmp))

    def test_companion_beside_the_epub(self):
        epub = calibre.list_books(self.library)[0]["epub"]
        self.assertIsNone(calibre.companion_for(epub))
        companion = epub[: -len(".epub")] + ".companion.md"
        with open(companion, "w", encoding="utf-8") as f:
            f.write("# Companion\n")
        self.assertEqual(calibre.companion_for(epub), companion)


class KeychainTest(TempHome):
    def test_secrets_file_stands_in(self):
        self.assertIsNone(keychain.get(keychain.READWISE))
        keychain.put(keychain.READWISE, {"token": "abc"})
        self.assertEqual(keychain.get(keychain.READWISE), {"token": "abc"})
        keychain.forget(keychain.READWISE)
        self.assertIsNone(keychain.get(keychain.READWISE))


class LibraryTest(TempHome):
    def setUp(self):
        super().setUp()
        self.store = Store()
        readwise.pull(self.store, fakes.TOKEN)
        self.books = calibre.list_books(self.library)

    def test_rows_join_readwise_and_calibre(self):
        rows = {r["title"]: r for r in library.list_books(self.store, self.books, {})}
        self.assertEqual(rows["Pride and Prejudice"]["calibre_id"], 1)
        self.assertEqual(rows["Pride and Prejudice"]["counts"]["pending"], 3)
        self.assertEqual(rows["Meditations"]["error"], library.NO_EPUB)

    def test_users_pick_wins_over_the_match(self):
        rw_book = self.store.readwise_book(fakes.PRIDE_ID)
        self.assertEqual(library.linked(rw_book, self.books, {str(fakes.PRIDE_ID): 2})["id"], 2)

    def test_load_book_places_highlights_in_the_epub(self):
        rw_book = self.store.readwise_book(fakes.PRIDE_ID)
        match = library.linked(rw_book, self.books, {})
        key = library.source_key(fakes.PRIDE_ID)
        book = library.load_book(self.store, book_id_for(key), rw_book, match)
        self.assertEqual([h["key"] for h in book["highlights"]], ["rw:1001", "rw:1002", "rw:1003"])
        first = book["highlights"][0]
        self.assertTrue(first["location"]["found"])
        self.assertEqual(first["chapter"], "Chapter I.")
        self.assertEqual(first["page"], 1)
        self.assertIn("single man", first["passage"]["mark"])
        self.assertEqual(book["highlights"][1]["note"], "Elizabeth teasing Darcy")
        self.assertTrue(book["chapters"])

    def test_load_book_without_epub_still_lists_highlights(self):
        rw_book = self.store.readwise_book(fakes.MEDITATIONS_ID)
        book = library.load_book(self.store, "x", rw_book, None)
        self.assertEqual(len(book["highlights"]), 1)
        self.assertFalse(book["highlights"][0]["location"]["found"])
        self.assertEqual(book["error"], library.NO_EPUB)


if __name__ == "__main__":
    unittest.main()
